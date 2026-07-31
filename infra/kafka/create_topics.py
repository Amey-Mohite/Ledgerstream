"""Create Kafka topics with INTENTIONAL partition counts.

Auto-create is disabled on the broker on purpose (docker-compose.yml), so topics
must be declared explicitly — that's what makes partition counts a deliberate
design choice, not an accident. Run once after the stack is up:

    python infra/kafka/create_topics.py

Partition strategy: `payments.events` / `ledger.events` get 6 partitions so we can
demonstrate parallel consumers and per-account ordering (key = hash(tenant,
account)). DLQ topics (Phase 3) get 1 partition — low volume, order irrelevant.
Replication factor is 1 (single local broker); production would use 3.
"""

from __future__ import annotations

import os
import sys

from confluent_kafka.admin import AdminClient, NewTopic

RF = 1  # single local broker; prod = 3

TOPICS = [
    NewTopic("payments.events", num_partitions=6, replication_factor=RF),
    NewTopic("ledger.events", num_partitions=6, replication_factor=RF),
    # Dead-letter queues — wired up in Phase 3.
    NewTopic("payments.events.dlq", num_partitions=1, replication_factor=RF),
    NewTopic("ledger.events.dlq", num_partitions=1, replication_factor=RF),
]


def main() -> int:
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
    admin = AdminClient({"bootstrap.servers": bootstrap})

    print(f"Creating topics on {bootstrap} ...")
    futures = admin.create_topics(TOPICS)
    failed = False
    for topic, future in futures.items():
        try:
            future.result()  # block until done
            print(f"  created  {topic}")
        except Exception as exc:  # noqa: BLE001
            if "already exists" in str(exc).lower():
                print(f"  exists   {topic}")
            else:
                print(f"  FAILED   {topic}: {exc}")
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
