"""Run the Payment saga consumer (compensates on LedgerRejected).

    python manage.py consume_ledger_outcomes
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from consumer.worker import SagaConsumer


class Command(BaseCommand):
    help = "Consume ledger.events and compensate rejected payments."

    def handle(self, *args, **options) -> None:
        SagaConsumer().run()
