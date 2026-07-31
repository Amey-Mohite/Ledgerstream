"""The outbox relay: ship PENDING outbox rows to Kafka.

This is the *second half* of the outbox pattern (the first half — the atomic write
— lives in payments/services.py). It runs as a **standalone worker process**, NOT
inside the web request cycle, because it's a long-lived poll loop.

Flow each cycle:
  1. read the oldest PENDING rows
  2. Avro-serialize each (schema auto-registered in the Schema Registry) and
     produce to its topic, keyed by partition_key (per-account ordering)
  3. after the broker acks, mark those rows PUBLISHED

Semantics: **at-least-once.** If the worker crashes after producing but before
marking PUBLISHED, the row stays PENDING and is re-published next cycle — a
duplicate event. That's fine because consumers are idempotent (dedupe on event_id).

**Runs safely as MULTIPLE instances (high availability):** each cycle claims a
batch of PENDING rows with `SELECT ... FOR UPDATE SKIP LOCKED`, so concurrent
relays lock *disjoint* rows and never double-publish. A crashed relay's rows roll
back to PENDING and are reclaimed by another instance.
"""

from __future__ import annotations

import logging
import signal
import time

from confluent_kafka import Producer
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext
from django.db import transaction
from django.utils import timezone

from ledgerstream_shared.kafka import (
    bootstrap_servers,
    load_avro_schema,
    schema_registry_client,
)
from outbox.models import OutboxEvent

logger = logging.getLogger(__name__)

# Which Avro schema serializes which event type.
SCHEMA_FOR_EVENT = {
    "PaymentCaptured": "payment_captured",
}


class OutboxRelay:
    def __init__(self, batch_size: int = 500, poll_interval: float = 1.0):
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self._stop = False

        sr = schema_registry_client()
        # One Avro serializer per event type. Creating it just LOADS the schema
        # text (from the .avsc file) — nothing is registered yet. The schema is
        # actually registered in the Schema Registry lazily, on the FIRST call to
        # this serializer (see run_once), because auto.register.schemas defaults to
        # True. After that first call the schema id is cached, so there are no more
        # registry round-trips. In production you'd set auto.register.schemas=False
        # and register schemas via CI instead (so a bad schema fails the pipeline).
        self._serializers = {
            event: AvroSerializer(sr, load_avro_schema(name))
            for event, name in SCHEMA_FOR_EVENT.items()
        }
        self.producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers(),
                # Idempotent producer: dedupes the producer's OWN retries to a
                # partition, so a network hiccup can't create duplicate events.
                "enable.idempotence": True,
                # Durability, PER MESSAGE: the broker acks a message only after the
                # partition leader AND all in-sync replicas have written it. So a
                # "delivered" callback means that event survives a broker failure.
                # (acks is a broker rule, not something we poll — poll/flush below
                # just collect the results of these acks.)
                "acks": "all",
            }
        )

    # -- event body -----------------------------------------------------------
    def _build_value(self, row: OutboxEvent) -> dict:
        # Returns a plain dict matching the Avro schema, e.g.:
        #   {
        #     "event_id": "3df7c828-e8ce-4da4-b13d-10e95e2d3c9a",
        #     "event_version": 1,
        #     "occurred_at": datetime(2026, 7, 30, 0, 50, 26, tzinfo=utc),
        #     "correlation_id": "be672f95425045f5967e1f81a25dc0bf",
        #     "tenant_id": "4072bc99-496e-46f3-b78f-4dd58d9fbab4",
        #     "payment_id": "c798f005-a0b0-4610-ba4a-645689f01178",
        #     "amount_minor": 500, "currency": "USD", "reference": "order-1",
        #   }
        p = row.payload
        return {
            "event_id": str(row.id),           # consumers dedupe on this
            "event_version": row.event_version,
            "occurred_at": row.created_at,      # aware datetime → timestamp-millis
            "correlation_id": row.correlation_id or "",
            "tenant_id": p["tenant_id"],
            "payment_id": p["payment_id"],
            "amount_minor": p["amount_minor"],
            "currency": p["currency"],
            "reference": p.get("reference", ""),
        }

    # -- one cycle ------------------------------------------------------------
    def run_once(self) -> int:
        # The whole cycle runs in ONE transaction that LOCKS the rows it publishes
        # (SELECT ... FOR UPDATE SKIP LOCKED). This makes the relay safe to run as
        # MULTIPLE instances (HA): each relay locks a disjoint set of PENDING rows;
        # other relays SKIP the locked ones and grab different rows → no
        # double-publishing. On a crash the tx rolls back → rows unlock → still
        # PENDING → reclaimed by another instance (at-least-once; consumers dedupe).
        #
        # Trade-off: the lock is held across the Kafka produce/flush (network I/O).
        # Fine for a bounded batch; at huge scale you'd instead "claim" rows (mark
        # them PROCESSING) in a short tx and reclaim stale claims on a timeout.
        delivered: list = []
        with transaction.atomic():
            rows = list(
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(status=OutboxEvent.Status.PENDING)
                .order_by("created_at")[: self.batch_size]
            )
            if not rows:
                return 0

            def make_cb(row_id):
                def _cb(err, _msg):
                    if err is not None:
                        logger.error(
                            "outbox delivery failed",
                            extra={"outbox_id": str(row_id), "error": str(err)},
                        )
                    else:
                        delivered.append(row_id)
                return _cb

            for row in rows:
                serializer = self._serializers.get(row.event_type)
                if serializer is None:
                    logger.error(
                        "no Avro schema for event_type",
                        extra={"event_type": row.event_type, "outbox_id": str(row.id)},
                    )
                    continue
                # Calling the serializer does two things: (1) on the FIRST call for
                # this subject it registers the schema and gets a schema id (cached
                # after); (2) encodes the dict to the Confluent wire format =
                # [magic byte][4-byte schema id][Avro binary]. The SerializationContext
                # names the subject: "<topic>-value", e.g. "payments.events-value".
                value_bytes = serializer(
                    self._build_value(row),
                    SerializationContext(row.topic, MessageField.VALUE),
                )
                # → value_bytes is `bytes` in the Confluent wire format, e.g.:
                #     b'\x00\x00\x00\x00\x01\x06USD\x88\x08...'
                #        │  └── id=1 ──┘ └── Avro binary (values only, no names) ──┘
                #        └ magic byte 0x00
                #   5-byte header (magic + schema id) + compact Avro body.
                self.producer.produce(
                    topic=row.topic,
                    key=row.partition_key,      # string key → per-account ordering
                    value=value_bytes,
                    on_delivery=make_cb(row.id),  # called later, when the broker acks
                )
                # produce() is ASYNC: it queues the message and returns; a background
                # thread sends it and collects the broker's ack, queuing a delivery
                # report. Our on_delivery callback only runs when we poll()/flush().
                #
                # poll(0) is the IN-LOOP PUMP: "run any delivery callbacks ready right
                # now, don't wait" (0 = non-blocking). Called each iteration so, during
                # a large batch, callbacks fire promptly and the report/memory queue
                # doesn't grow unbounded. flush() after the loop is the final wait-for-all.
                self.producer.poll(0)

            # flush() is the FINAL WAIT-FOR-ALL barrier: block until every queued
            # message is delivered and every on_delivery callback has fired. Only then
            # is `delivered` complete — so we can safely mark exactly those rows
            # PUBLISHED below. (poll(0) in the loop pumps incrementally; flush() is the
            # one blocking "wait for the whole batch to finish".)
            self.producer.flush()

            if delivered:
                OutboxEvent.objects.filter(id__in=delivered).update(
                    status=OutboxEvent.Status.PUBLISHED,
                    published_at=timezone.now(),
                )
        return len(delivered)   # → int, e.g. 2 (how many rows were published this cycle)

    # -- run loop -------------------------------------------------------------
    def run(self) -> None:
        self._install_signal_handlers()
        logger.info("outbox relay started")
        while not self._stop:
            try:
                published = self.run_once()
                if published:
                    logger.info("relayed events", extra={"count": published})
                else:
                    time.sleep(self.poll_interval)
            except Exception:  # noqa: BLE001 — never let the loop die silently
                logger.exception("outbox relay cycle failed")
                time.sleep(self.poll_interval)
        self.producer.flush()               # drain before exit (graceful shutdown)
        logger.info("outbox relay stopped")

    def _install_signal_handlers(self) -> None:
        """Turn OS stop-signals into a graceful shutdown.

        By default SIGINT/SIGTERM kill the process instantly — which could
        interrupt a publish. Instead we register a handler that only flips
        `self._stop = True`, so the run() loop finishes its current cycle, flushes
        in-flight messages, and exits cleanly.
        """

        def handler(signum, _frame):
            # Runs when a signal arrives. Do the minimum: ask the loop to stop.
            logger.info("shutdown signal received", extra={"signal": signum})
            self._stop = True

        signal.signal(signal.SIGINT, handler)   # Ctrl+C in a terminal
        try:
            # SIGTERM = "please stop" from `kill` or a container/orchestrator stop.
            signal.signal(signal.SIGTERM, handler)
        except (ValueError, AttributeError):
            # Windows doesn't fully support SIGTERM — register where we can, ignore
            # where we can't (SIGINT still works).
            pass
