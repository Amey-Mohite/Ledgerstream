"""Kafka + Schema Registry helpers shared by producers and consumers.

Importing this module requires the `kafka` extra
(`pip install ledgerstream-shared[kafka]`) — it's kept out of the package's core
imports so services that don't touch Kafka stay lightweight.

Provides: env-driven connection config, a Schema Registry client factory, and an
Avro-schema loader that finds `schemas/avro/<name>.avsc` in the repo.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, TypeVar

from confluent_kafka.schema_registry import SchemaRegistryClient

from ledgerstream_shared.config import get_env

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_with_retry(fn: Callable[[], T], *, max_retries: int = 3, base_delay: float = 0.5) -> T:
    """Call ``fn()``; on exception, retry with EXPONENTIAL BACKOFF.

    This distinguishes a *transient* failure (a DB blip, a broker hiccup) from a
    *poison* message. Transient failures usually clear within a few tries, so we
    wait base_delay, 2·base_delay, 4·base_delay … between attempts. If it still
    fails after `max_retries`, we re-raise the last exception — the caller then
    routes the message to a DLQ instead of blocking the partition forever.

    Total attempts = max_retries + 1 (the first try is not a "retry").
    """
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception:  # noqa: BLE001 — retry policy is exception-agnostic by design
            if attempt == max_retries:
                raise                       # exhausted → let the caller DLQ
            delay = base_delay * (2 ** attempt)
            logger.warning(
                "handler failed, backing off before retry",
                extra={"attempt": attempt + 1, "max_retries": max_retries, "delay_s": delay},
            )
            time.sleep(delay)
    raise AssertionError("unreachable")     # loop either returns or raises


def bootstrap_servers() -> str:
    """Kafka bootstrap servers (host listener by default for native processes)."""
    return get_env("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")


def schema_registry_url() -> str:
    return get_env("SCHEMA_REGISTRY_URL", "http://localhost:8081")


def schema_registry_client() -> SchemaRegistryClient:
    return SchemaRegistryClient({"url": schema_registry_url()})


def _schema_dir() -> Path:
    """Locate the shared `schemas/avro` directory.

    Uses AVRO_SCHEMA_DIR if set (containers set this to the COPYed path);
    otherwise walks up from the current directory to find `schemas/avro`.
    """
    override = get_env("AVRO_SCHEMA_DIR")
    if override:
        return Path(override)
    start = Path.cwd()
    for parent in [start, *start.parents]:
        candidate = parent / "schemas" / "avro"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "Could not locate schemas/avro; set AVRO_SCHEMA_DIR to its path."
    )


def load_avro_schema(name: str) -> str:
    """Return the Avro schema text for `<name>.avsc` (e.g. 'payment_captured')."""
    return (_schema_dir() / f"{name}.avsc").read_text(encoding="utf-8")
