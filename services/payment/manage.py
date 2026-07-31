#!/usr/bin/env python
"""Django management entrypoint for the Payment service."""
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Couldn't import Django. Is it installed and is your virtual "
            "environment activated?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
