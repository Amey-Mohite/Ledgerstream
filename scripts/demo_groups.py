#!/usr/bin/env python
"""One-shot demo: prove consumer-GROUP behaviour on `payments.events`.

Creates THREE consumers in ONE process and prints the partitions each ends up
owning after the group settles:
  * demoA/A1 + demoA/A2  → SAME group → they SPLIT the 6 partitions (disjoint)
  * demoB/B1             → DIFFERENT group → gets ALL 6 (a full copy)

Run from the repo root:  ./.venv/Scripts/python scripts/demo_groups.py
(For the interactive, multi-terminal version, use scripts/demo_consumer.py.)
"""

from __future__ import annotations

import time
import uuid

from confluent_kafka import Consumer

from ledgerstream_shared.kafka import bootstrap_servers

TOPIC = "payments.events"
SUFFIX = uuid.uuid4().hex[:6]   # fresh groups each run (read from earliest, no old offsets)


def make(group: str) -> Consumer:
    c = Consumer(
        {
            "bootstrap.servers": bootstrap_servers(),
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        }
    )
    c.subscribe([TOPIC])
    return c


def main() -> None:
    a1 = make(f"demoA-{SUFFIX}")
    a2 = make(f"demoA-{SUFFIX}")   # SAME group as a1
    b1 = make(f"demoB-{SUFFIX}")   # DIFFERENT group
    consumers = {"demoA/A1": a1, "demoA/A2": a2, "demoB/B1": b1}
    events = {name: 0 for name in consumers}

    print(f"topic={TOPIC}  (groups: demoA-{SUFFIX} x2, demoB-{SUFFIX} x1)")
    print("polling ~12s to let the group coordinator assign + rebalance...\n")

    deadline = time.time() + 12
    while time.time() < deadline:
        for name, c in consumers.items():
            msg = c.poll(0.1)
            if msg is not None and not msg.error():
                events[name] += 1

    print("FINAL partition assignments:")
    for name, c in consumers.items():
        parts = sorted(p.partition for p in c.assignment())
        print(f"  {name:10s}  partitions={parts}  (events seen: {events[name]})")

    a1_parts = {p.partition for p in a1.assignment()}
    a2_parts = {p.partition for p in a2.assignment()}
    b1_parts = {p.partition for p in b1.assignment()}
    print()
    print(f"demoA split disjoint?  A1 AND A2 overlap = {sorted(a1_parts & a2_parts)} (empty = yes, split)")
    print(f"demoA covers all?      A1 OR  A2 union   = {sorted(a1_parts | a2_parts)}")
    print(f"demoB full copy?       B1               = {sorted(b1_parts)}")

    for c in consumers.values():
        c.close()


if __name__ == "__main__":
    main()
