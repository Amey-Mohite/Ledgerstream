"""Kafka + Schema Registry helpers shared by producers and consumers.

Importing this module requires the `kafka` extra
(`pip install ledgerstream-shared[kafka]`) — it's kept out of the package's core
imports so services that don't touch Kafka stay lightweight.

Provides: env-driven connection config, a Schema Registry client factory, and an
Avro-schema loader that finds `schemas/avro/<name>.avsc` in the repo.
"""

from __future__ import annotations

from pathlib import Path

from confluent_kafka.schema_registry import SchemaRegistryClient

from ledgerstream_shared.config import get_env


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
