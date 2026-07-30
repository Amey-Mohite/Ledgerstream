"""Typed environment access.

A thin, dependency-free wrapper over os.environ. We deliberately do NOT read a
`.env` file here — in containers the environment is populated by docker-compose
(and in prod by a secrets manager). Parsing `.env` is a dev-shell concern, not a
runtime concern. Keeping that boundary clean is why there is no python-dotenv
dependency in the shared library.
"""

from __future__ import annotations

import os

_MISSING = object()


class ConfigError(RuntimeError):
    """Raised when a required environment variable is absent."""


def get_env(name: str, default: str | None = None) -> str | None:
    """Return an env var, or `default` if unset."""
    return os.environ.get(name, default)


def require_env(name: str) -> str:
    """Return an env var, or raise ConfigError if unset/empty.

    Use this for values the service cannot safely start without (DB URL, signing
    key). Failing fast at boot beats a NoneType error deep in a request.
    """
    value = os.environ.get(name, _MISSING)
    if value is _MISSING or value == "":
        raise ConfigError(f"Required environment variable '{name}' is not set")
    return value  # type: ignore[return-value]


def get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable '{name}' must be an int, got {raw!r}") from exc
