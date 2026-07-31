#!/usr/bin/env python
"""Demo consumer for watching consumer-GROUP behaviour on `payments.events`.

It just reads and PRINTS events (it doesn't post anything), so you can safely run
several instances to SEE how groups work — without touching the real ledger
consumer's offsets.

    # two consumers in the SAME group → they SPLIT the 6 partitions (work shared):
    python scripts/demo_consumer.py --group groupA --id A1
    python scripts/demo_consumer.py --group groupA --id A2

    # a consumer in a DIFFERENT group → gets a FULL copy of every event (fan-out):
    python scripts/demo_consumer.py --group groupB --id B1

Each instance logs the partitions it's ASSIGNED (so you see the split), and every
event it receives. Produce events by capturing payments + running the relay, or
just start with existing events (auto.offset.reset=earliest).

Run from the repo root so it finds the venv + Kafka on localhost.
"""

from __future__ import annotations

import argparse
import signal

from confluent_kafka import Consumer
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext

from ledgerstream_shared.kafka import bootstrap_servers, schema_registry_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True, help="consumer group id")
    ap.add_argument("--id", default="?", help="label for this instance (logs only)")
    ap.add_argument("--topic", default="payments.events")
    args = ap.parse_args()
    tag = f"[{args.group}/{args.id}]"

    deserializer = AvroDeserializer(schema_registry_client())
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_servers(),
            "group.id": args.group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,   # demo only: auto-commit is fine here
        }
    )

    def on_assign(_c, parts):
        print(f"{tag} ASSIGNED partitions {sorted(p.partition for p in parts)}", flush=True)

    def on_revoke(_c, parts):
        print(f"{tag} REVOKED  partitions {sorted(p.partition for p in parts)}", flush=True)

    consumer.subscribe([args.topic], on_assign=on_assign, on_revoke=on_revoke)
    print(f"{tag} started on '{args.topic}' (Ctrl-C to stop)", flush=True)

    stop = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(v=True))

    try:
        while not stop["v"]:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"{tag} error: {msg.error()}", flush=True)
                continue
            e = deserializer(
                msg.value(), SerializationContext(args.topic, MessageField.VALUE)
            )
            print(
                f"{tag} got p{msg.partition()}@{msg.offset()} "
                f"event={e['event_id'][:8]} tenant={e['tenant_id'][:8]} "
                f"amount={e['amount_minor']} {e['currency']}",
                flush=True,
            )
    finally:
        consumer.close()
        print(f"{tag} stopped", flush=True)


if __name__ == "__main__":
    main()
