"""The Ledger's Kafka consumer worker (standalone process, not the web cycle).

Consumes `payments.events`, deserializes Avro (schema fetched from the registry by
the id in each message), and posts a double-entry journal — idempotently.

Delivery is **at-least-once**: we commit the Kafka offset only AFTER the DB write
succeeds. If we crash between processing and commit, the event is redelivered and
`post_payment_captured` no-ops on it (dedupe on event_id).

Failure handling (Phase 3): a transient error (DB/broker blip) is retried with
exponential backoff; if it still fails — or the message can't even be
deserialized (a *poison* message) — the raw bytes are parked on a DLQ and the
offset is committed, so one bad message can't block the partition forever.
"""

from __future__ import annotations

import datetime
import logging
import signal

from confluent_kafka import Consumer, Producer
from confluent_kafka.schema_registry.avro import AvroDeserializer, AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext

from ledger.services import post_payment_captured
from ledgerstream_shared.correlation import reset_correlation_id, set_correlation_id
from ledgerstream_shared.kafka import (
    bootstrap_servers,
    load_avro_schema,
    run_with_retry,
    schema_registry_client,
)

logger = logging.getLogger(__name__)

TOPIC = "payments.events"
GROUP_ID = "ledger-service"      # one consumer GROUP = the Ledger service
OUTCOME_TOPIC = "ledger.events"  # we emit LedgerOutcome here for the Payment saga
DLQ_TOPIC = TOPIC + ".dlq"       # poison messages are parked here (raw bytes)


class LedgerConsumer:
    def __init__(self):
        self._stop = False
        sr = schema_registry_client()
        self._deserializer = AvroDeserializer(sr)
        # No outbox needed here: our SOURCE is Kafka (replayable). We post the
        # journal, then produce the outcome, then commit the offset LAST — a crash
        # anywhere before the commit redelivers, and both steps are idempotent, so
        # replay converges. (Outbox is only for non-replayable API-sourced events.)
        self._outcome_serializer = AvroSerializer(sr, load_avro_schema("ledger_outcome"))
        self._producer = Producer(
            {"bootstrap.servers": bootstrap_servers(), "enable.idempotence": True, "acks": "all"}
        )
        self.consumer = Consumer(
            {
                "bootstrap.servers": bootstrap_servers(),
                "group.id": GROUP_ID,
                "auto.offset.reset": "earliest",   # first run reads history
                "enable.auto.commit": False,        # we commit after processing
            }
        )

    def run(self) -> None:
        self._install_signal_handlers()
        # on_assign/on_revoke fire during a REBALANCE (a consumer joins/leaves the
        # group). Logging the assigned partitions makes group behaviour visible:
        # run TWO consumers in this group and each logs a DIFFERENT subset of
        # partitions (the work is split); a consumer in a DIFFERENT group would get
        # ALL partitions (a full copy). This is "consumer-group rebalancing
        # awareness" in practice.
        self.consumer.subscribe(
            [TOPIC], on_assign=self._on_assign, on_revoke=self._on_revoke
        )
        logger.info("ledger consumer started", extra={"topic": TOPIC, "group": GROUP_ID})
        try:
            while not self._stop:
                # Ask Kafka for the next message, waiting UP TO 1.0 second.
                # - a message ready → returns it immediately
                # - nothing within 1s → returns None (we loop and re-check _stop)
                # The 1s timeout is what keeps shutdown responsive: even when idle,
                # we come back around at least once a second to notice self._stop.
                msg = self.consumer.poll(1.0)
                # → msg is None (timeout), or a Message object with:
                #     msg.value()     -> bytes (the Avro wire-format bytes)
                #     msg.key()       -> b'4072bc99...:c798f005...' (partition key)
                #     msg.topic()     -> 'payments.events'
                #     msg.partition() -> 3     msg.offset() -> 0
                #     msg.error()     -> None, or an error to handle
                if msg is None:
                    continue                      # timed out with no message — loop
                if msg.error():
                    logger.error("consume error", extra={"error": str(msg.error())})
                    continue
                self._handle(msg)
        finally:
            self.consumer.close()   # leaves the group cleanly (triggers rebalance)
            logger.info("ledger consumer stopped")

    def _handle(self, msg) -> None:
        # Reads the schema id from the message header, fetches that schema from the
        # registry (cached), and decodes the Avro bytes back into a plain dict:
        #   event → {"event_id": "3df7c828-...", "tenant_id": "4072bc99-...",
        #            "payment_id": "c798f005-...", "amount_minor": 500,
        #            "currency": "USD", "occurred_at": datetime(...), ...}
        try:
            event = self._deserializer(
                msg.value(), SerializationContext(TOPIC, MessageField.VALUE)
            )
        except Exception:  # noqa: BLE001 — undeserializable = poison, retrying can't help
            logger.exception(
                "could not deserialize message → DLQ",
                extra={"offset": msg.offset(), "partition": msg.partition()},
            )
            self._to_dlq(msg)
            self.consumer.commit(msg)   # move past the poison message
            return
        # Propagate the correlation id from the event so the ledger's logs share
        # the same id as the originating payment request.
        cid = (event or {}).get("correlation_id", "")
        token = set_correlation_id(cid) if cid else None
        try:
            # Both steps are idempotent, so retrying the whole unit is safe:
            # post_payment_captured dedupes on event_id, _emit_outcome on a
            # deterministic outcome id.
            run_with_retry(lambda: self._process(event))
            # commit() advances THIS consumer group's stored offset past msg, so on
            # restart/rebalance we resume AFTER it and never reread it. We commit
            # LAST — only after the journal is posted and the outcome is confirmed
            # delivered — which is what makes delivery at-least-once: a crash before
            # this line redelivers msg, and both steps are idempotent so replay
            # converges. (Committing BEFORE processing would be at-most-once: a crash
            # would skip msg forever.)
            self.consumer.commit(msg)
        except Exception:  # noqa: BLE001 — retries exhausted → don't block the partition
            logger.exception(
                "processing failed after retries → DLQ",
                extra={"offset": msg.offset(), "partition": msg.partition()},
            )
            self._to_dlq(msg)
            self.consumer.commit(msg)   # msg is safe on the DLQ → commit past it so it can't block the partition
        finally:
            if token is not None:
                reset_correlation_id(token)

    def _process(self, event: dict) -> None:
        status, reason = post_payment_captured(event)   # POSTED or REJECTED
        self._emit_outcome(event, status, reason)        # produce, confirm delivery

    def _to_dlq(self, msg) -> None:
        """Park the RAW original bytes on the DLQ so nothing is lost and the message
        can be inspected / replayed later. We reuse the outcome producer."""
        self._producer.produce(DLQ_TOPIC, key=msg.key(), value=msg.value())
        self._producer.flush()   # block until the DLQ write is confirmed, before we commit
        logger.error("routed to DLQ", extra={"dlq": DLQ_TOPIC, "offset": msg.offset()})

    def _emit_outcome(self, event: dict, status: str, reason: str) -> None:
        """Produce the LedgerOutcome and BLOCK until it's delivered.

        Raising on non-delivery keeps the offset uncommitted → redeliver → the
        outcome is re-emitted (payment saga dedupes on the deterministic event_id).
        """
        value = {
            "event_id": "outcome:" + event["event_id"],   # deterministic → dedupe
            "occurred_at": datetime.datetime.now(datetime.timezone.utc),
            "correlation_id": event.get("correlation_id", ""),
            "tenant_id": event["tenant_id"],
            "payment_id": event["payment_id"],
            "status": status,
            "reason": reason,
        }
        value_bytes = self._outcome_serializer(
            value, SerializationContext(OUTCOME_TOPIC, MessageField.VALUE)
        )
        delivered = {"ok": False}
        self._producer.produce(
            OUTCOME_TOPIC,
            key=f"{event['tenant_id']}:{event['payment_id']}",   # per-payment ordering
            value=value_bytes,
            on_delivery=lambda err, _m: delivered.update(ok=err is None),
        )
        # produce() is ASYNCHRONOUS — it only enqueues the message in the producer's
        # internal buffer and returns immediately (nothing is on the wire yet).
        # flush() BLOCKS until every buffered message has actually been sent to the
        # broker AND its on_delivery callback has fired. So only after flush()
        # returns is `delivered["ok"]` trustworthy — this is what turns the async
        # producer into a synchronous "confirm the outcome landed before we commit".
        self._producer.flush()
        if not delivered["ok"]:
            raise RuntimeError("LedgerOutcome not delivered")
        logger.info(
            "emitted LedgerOutcome",
            extra={"payment_id": event["payment_id"], "status": status, "reason": reason},
        )

    def _on_assign(self, consumer, partitions) -> None:
        logger.info(
            "partitions ASSIGNED to this consumer",
            extra={"group": GROUP_ID, "partitions": sorted(p.partition for p in partitions)},
        )

    def _on_revoke(self, consumer, partitions) -> None:
        logger.info(
            "partitions REVOKED from this consumer",
            extra={"group": GROUP_ID, "partitions": sorted(p.partition for p in partitions)},
        )

    def _install_signal_handlers(self) -> None:
        """Graceful shutdown: turn SIGINT/SIGTERM into `self._stop = True`.

        The handler only flips the flag; the run() loop then leaves cleanly and
        `consumer.close()` commits final offsets + leaves the group (triggering a
        rebalance) — instead of the process being killed mid-message.
        """

        def handler(signum, _frame):
            logger.info("shutdown signal received", extra={"signal": signum})
            self._stop = True

        signal.signal(signal.SIGINT, handler)   # Ctrl+C
        try:
            signal.signal(signal.SIGTERM, handler)   # kill / orchestrator stop
        except (ValueError, AttributeError):
            pass                                  # SIGTERM limited on Windows
