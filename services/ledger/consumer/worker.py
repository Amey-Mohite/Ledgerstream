"""The Ledger's Kafka consumer worker (standalone process, not the web cycle).

Consumes `payments.events`, deserializes Avro (schema fetched from the registry by
the id in each message), and posts a double-entry journal — idempotently.

Delivery is **at-least-once**: we commit the Kafka offset only AFTER the DB write
succeeds. If we crash between processing and commit, the event is redelivered and
`post_payment_captured` no-ops on it (dedupe on event_id). Proper retry/backoff +
dead-letter routing for *poison* messages arrives in Phase 3.
"""

from __future__ import annotations

import logging
import signal

from confluent_kafka import Consumer
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext

from ledger.services import post_payment_captured
from ledgerstream_shared.correlation import reset_correlation_id, set_correlation_id
from ledgerstream_shared.kafka import bootstrap_servers, schema_registry_client

logger = logging.getLogger(__name__)

TOPIC = "payments.events"
GROUP_ID = "ledger-service"      # one consumer GROUP = the Ledger service


class LedgerConsumer:
    def __init__(self):
        self._stop = False
        self._deserializer = AvroDeserializer(schema_registry_client())
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
        event = self._deserializer(
            msg.value(), SerializationContext(TOPIC, MessageField.VALUE)
        )
        # Propagate the correlation id from the event so the ledger's logs share
        # the same id as the originating payment request.
        cid = (event or {}).get("correlation_id", "")
        token = set_correlation_id(cid) if cid else None
        try:
            post_payment_captured(event)
            self.consumer.commit(msg)   # commit AFTER success → at-least-once
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to process event (offset not committed)",
                extra={"offset": msg.offset(), "partition": msg.partition()},
            )
            # No commit → reprocessed on restart. Phase 3 adds retry/backoff + DLQ.
        finally:
            if token is not None:
                reset_correlation_id(token)

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
