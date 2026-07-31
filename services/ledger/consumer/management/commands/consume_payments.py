"""Run the Ledger's payments.events consumer (standalone worker process).

    python manage.py consume_payments

Ctrl-C / SIGTERM shuts it down gracefully (leaves the consumer group cleanly).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from consumer.worker import LedgerConsumer


class Command(BaseCommand):
    help = "Consume payments.events and post double-entry ledger journals."

    def handle(self, *args, **options) -> None:
        LedgerConsumer().run()
