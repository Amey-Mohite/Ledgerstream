"""Run the outbox relay worker (standalone process, not the web cycle).

    python manage.py run_outbox_relay

Publishes PENDING outbox rows to Kafka (Avro) and marks them PUBLISHED. Ctrl-C
(SIGINT) / SIGTERM shuts it down gracefully after draining in-flight messages.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from outbox.relay import OutboxRelay


class Command(BaseCommand):
    help = "Run the outbox relay: publish PENDING outbox rows to Kafka."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--poll-interval", type=float, default=1.0)

    def handle(self, *args, **options) -> None:
        OutboxRelay(
            batch_size=options["batch_size"],
            poll_interval=options["poll_interval"],
        ).run()
