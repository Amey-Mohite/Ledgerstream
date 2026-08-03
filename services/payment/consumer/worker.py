"""Payment SAGA consumer (standalone worker).

Consumes `ledger.events` (LedgerOutcome). On REJECTED it runs the **compensating
action** (void the payment) — the failure path of the Payment -> Ledger saga. On
POSTED it's a no-op (the payment is already CAPTURED; nothing to do).

Idempotent by state: compensation voids a CAPTURED payment and no-ops if already
VOIDED, so redeliveries are safe (no dedupe table needed). At-least-once: commit
the offset only after handling.

Failure handling (Phase 3): a transient compensation failure is retried with
exponential backoff; if it still fails — or the outcome can't be deserialized —
the raw bytes go to a DLQ and the offset is committed, so one bad message can't
block the partition.
"""

from __future__ import annotations

import logging
import signal

from confluent_kafka import Consumer, Producer
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext

from ledgerstream_shared.correlation import reset_correlation_id, set_correlation_id
from ledgerstream_shared.kafka import (
    bootstrap_servers,
    run_with_retry,
    schema_registry_client,
)
from payments.services import compensate_payment

logger = logging.getLogger(__name__)

TOPIC = "ledger.events"
GROUP_ID = "payment-saga"
DLQ_TOPIC = TOPIC + ".dlq"       # poison outcomes are parked here (raw bytes)


class SagaConsumer:
    def __init__(self):
        self._stop = False
        self._deserializer = AvroDeserializer(schema_registry_client())
        self._producer = Producer({"bootstrap.servers": bootstrap_servers()})
        self.consumer = Consumer(
            {
                "bootstrap.servers": bootstrap_servers(),
                "group.id": GROUP_ID,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )

    def run(self) -> None:
        self._install_signal_handlers()
        self.consumer.subscribe([TOPIC], on_assign=self._on_assign)
        logger.info("saga consumer started", extra={"topic": TOPIC, "group": GROUP_ID})
        try:
            while not self._stop:
                msg = self.consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    logger.error("consume error", extra={"error": str(msg.error())})
                    continue
                self._handle(msg)
        finally:
            self.consumer.close()
            logger.info("saga consumer stopped")

    def _handle(self, msg) -> None:
        try:
            outcome = self._deserializer(
                msg.value(), SerializationContext(TOPIC, MessageField.VALUE)
            )
        except Exception:  # noqa: BLE001 — undeserializable = poison, retrying can't help
            logger.exception("could not deserialize outcome → DLQ", extra={"offset": msg.offset()})
            self._to_dlq(msg)
            self.consumer.commit(msg)   # move past the poison message
            return
        token = set_correlation_id(outcome.get("correlation_id", "") or "")
        try:
            # compensate_payment is idempotent by state, so retrying is safe.
            run_with_retry(lambda: self._compensate_if_rejected(outcome))
            # commit() advances this group's stored offset past msg so we never
            # reprocess it after a restart/rebalance. We commit LAST (after the
            # compensation), which makes delivery at-least-once: a crash before this
            # line redelivers msg, and compensate_payment is idempotent by state so
            # replay is safe. (Committing first would be at-most-once → risk skipping
            # a rejection and leaving a payment wrongly CAPTURED.)
            self.consumer.commit(msg)
        except Exception:  # noqa: BLE001 — retries exhausted → don't block the partition
            logger.exception("saga handling failed after retries → DLQ", extra={"offset": msg.offset()})
            self._to_dlq(msg)
            self.consumer.commit(msg)   # msg is safe on the DLQ → commit past it so it can't block the partition
        finally:
            reset_correlation_id(token)

    def _compensate_if_rejected(self, outcome: dict) -> None:
        if outcome["status"] == "REJECTED":
            compensate_payment(
                payment_id=outcome["payment_id"], reason=outcome.get("reason", "")
            )

    def _to_dlq(self, msg) -> None:
        """Park the raw bytes on the DLQ so a poison outcome can't block the partition."""
        self._producer.produce(DLQ_TOPIC, key=msg.key(), value=msg.value())
        # produce() only enqueues (async); flush() blocks until the DLQ write is
        # actually delivered — so the message is safely parked before we commit.
        self._producer.flush()
        logger.error("routed to DLQ", extra={"dlq": DLQ_TOPIC, "offset": msg.offset()})

    def _on_assign(self, consumer, partitions) -> None:
        logger.info(
            "partitions ASSIGNED",
            extra={"group": GROUP_ID, "partitions": sorted(p.partition for p in partitions)},
        )

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            self._stop = True

        signal.signal(signal.SIGINT, handler)
        try:
            signal.signal(signal.SIGTERM, handler)
        except (ValueError, AttributeError):
            pass
