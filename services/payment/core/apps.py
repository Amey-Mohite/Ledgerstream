from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self) -> None:
        # Configure structured JSON logging + OTel tracing exactly once per
        # process, after Django has loaded. Runs for the web server AND for
        # management commands / worker processes alike.
        from django.conf import settings

        from ledgerstream_shared.logging import configure_logging
        from ledgerstream_shared.tracing import configure_tracing

        configure_logging(settings.SERVICE_NAME, level=settings.LOG_LEVEL)
        configure_tracing(settings.SERVICE_NAME)
