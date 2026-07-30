# Schema Registry — a deep dive

> **In one sentence:** a Schema Registry is a standalone service that stores every
> version of your message schemas, hands each a unique ID, and **enforces
> compatibility rules** when schemas change — so producers and consumers that were
> written and deployed independently can still understand each other's data.

> 🧊 **In plain terms:** imagine thousands of parcels flying between depots, each
> packed in a box whose exact layout matters to whoever unpacks it. Instead of
> printing the full packing-layout on every single parcel (wasteful) — or letting
> anyone silently change the layout and break the unpackers — you keep **one
> master catalogue** of every layout, numbered. Each parcel carries just the
> **layout number**. The unpacker reads the number, looks up the layout in the
> catalogue, and unpacks correctly. And the catalogue refuses to file a new layout
> that would break people still using the old one. That catalogue is the Schema
> Registry.

> This is the **Schema Registry component** deep-dive. The general theory of
> *why* schemas evolve and the compatibility rules (backward/forward/full) live in
> [Schema Evolution & Contracts](schema-evolution-and-contracts.md) — read that
> for the "why"; this file is the "what and how" of the registry itself.

---

## 1. The problem it solves (recap)

Two independently deployed programs exchange binary messages over Kafka. For a
consumer to decode a message, it must know the message's exact structure (the
schema). Three bad options if you *don't* have a registry:

1. **Embed the full schema in every message** — huge overhead; Avro/Protobuf are
   binary precisely to be small, and this throws that away.
2. **Hardcode the schema in each service** — the instant a producer changes its
   schema, every consumer built against the old one breaks, and you can't upgrade
   them all at the same instant.
3. **Coordinate by hand** ("everyone please update to v2 on Friday") — fragile,
   doesn't scale, and old retained messages still use old schemas.

A Schema Registry replaces all three: schemas live in **one shared service**,
messages carry only a tiny **ID**, and a **compatibility check** blocks breaking
changes *before* they ship.

---

## 2. Core concepts: subject, version, schema ID, compatibility

Four terms; get these straight and the rest follows.

```
  SUBJECT  "payments.events-value"        <- a named evolution scope (usually per topic+key/value)
     |
     +-- version 1  --> schema ID 42       (registered first)
     +-- version 2  --> schema ID 57       (added a field with a default)
     +-- version 3  --> schema ID 63       (added another field)
                          ^
                          |
        schema IDs are GLOBAL & unique across the whole registry;
        versions are LOCAL, numbered 1,2,3... within one subject.
```

- **Subject** — a named scope under which a schema evolves over time. By the
  default strategy it's derived from the topic: `myTopic-value` and `myTopic-key`.
  A subject's compatibility rule governs how its schema may change.
- **Version** — the sequence number of a schema *within a subject* (1, 2, 3…).
  A new compatible schema registered to a subject becomes the next version.
- **Schema ID** — a **globally unique** integer the registry assigns to each
  distinct schema. This is the number embedded in every message (not the version).
  Identical schemas registered under different subjects share the same ID
  (deduplicated).
- **Compatibility level** — the rule (`BACKWARD`, `FORWARD`, `FULL`, `NONE`,
  transitive variants) the registry enforces when you try to register a new
  version. Set globally and/or per-subject.

> **ID vs version — the classic mix-up:** the message carries the **global schema
> ID**, not the per-subject version. Version is a human-facing convenience;
> ID is what the wire protocol uses.

---

## 3. The wire format (the crucial internal detail)

This is the part people don't realize. When a producer serializes a record with a
schema-registry-aware serializer, the bytes on the Kafka message are **not** just
the Avro payload. They're prefixed with a tiny header:

```
 Kafka message value bytes (Confluent wire format):

 +----------+---------------------+-------------------------------+
 | 1 byte   | 4 bytes             | N bytes                       |
 | magic=0  | schema ID (int32)   | Avro-encoded payload          |
 +----------+---------------------+-------------------------------+
    ^            ^                     ^
    |            |                     +-- the actual data, no field names
    |            +-- big-endian; tells the consumer WHICH schema to fetch
    +-- format version marker (currently 0)
```

- **Magic byte (1 byte, value `0`)** — marks "this is the Confluent schema-registry
  format," so tooling knows how to read the rest.
- **Schema ID (4 bytes)** — the global ID from §2. This is the whole trick: **5
  bytes of header replace shipping the entire schema.**
- **Payload** — the Avro binary: values only, in schema order, **no field names**
  (that's why it's so compact, and why you *need* the schema to decode it).

> 🧊 **In plain terms:** every message wears a tiny 5-byte name tag that says
> "decode me with catalogue entry #57." Without the tag you'd be staring at raw
> bytes with no idea where one field ends and the next begins.

---

## 4. How produce & consume actually work (end to end)

The registry sits *beside* Kafka; data still flows through Kafka. The registry is
consulted for **schemas**, not for messages.

```
 PRODUCE
 -------
   app object ---> Avro serializer
                        |
                        | 1. "here's my schema" -> look up its ID
                        v
                  [ Schema Registry ]  (REST over HTTP)
                        |  2. returns/assigns schema ID (e.g. 57)
                        |     (checks compatibility if it's new)
                        v
   3. build bytes: [magic 0][id 57][avro payload]  ---> 4. produce to Kafka topic


 CONSUME
 -------
   Kafka message ---> Avro deserializer
                        |  1. read header -> magic 0, schema ID 57
                        v
                  [ Schema Registry ]
                        |  2. "give me schema for ID 57" -> returns writer schema
                        v
   3. decode Avro payload using writer schema
      (optionally resolve against the consumer's own READER schema)  ---> app object
```

Two performance facts that make this cheap:
- **Clients cache aggressively.** A serializer/deserializer caches schema↔ID
  mappings in memory, so it hits the registry roughly **once per schema**, not
  once per message. Millions of messages, a handful of registry calls.
- **The registry is off the hot path.** After the cache warms, produce/consume
  talks only to Kafka. If the registry is briefly down, already-cached schemas
  keep working (you just can't register/resolve a *brand-new* one).

### Writer schema vs reader schema (Avro's superpower)
The schema the producer used is the **writer schema**. The consumer may have its
own **reader schema** (the version its code expects). Avro performs **schema
resolution** — it reconciles the two field by field: fields the reader added (with
defaults) get their default, fields the writer had that the reader dropped are
skipped. **This is the mechanism that makes backward/forward compatibility
actually work at decode time**, and it's why "add fields with defaults" is the
golden rule.

---

## 5. How the registry stores schemas (it's backed by Kafka!)

The elegant internal detail: **Confluent Schema Registry stores its data in
Kafka itself** — a special, single-partition, **log-compacted** topic named
`_schemas`.

```
   _schemas topic (log-compacted, 1 partition)
   +--------------------------------------------------+
   | {subject: payments.events-value, version:1, id:42, schema:"..."} |
   | {subject: payments.events-value, version:2, id:57, schema:"..."} |
   | {config:  payments.events-value, compat: BACKWARD}               |
   +--------------------------------------------------+
                     |
                     v
       registry instances consume this topic and build an
       in-memory index (subject -> versions -> schema, id -> schema)
```

Consequences worth knowing:
- **Durability & replay for free** — the registry's own state is a Kafka log, so
  it survives restarts by replaying `_schemas`, and inherits Kafka's replication.
- **Log compaction** keeps the latest record per key, so the topic doesn't grow
  unbounded while retaining current truth.
- **Leader/primary election** — when you run multiple registry instances for HA,
  **only one is the leader** and may *write* new schemas (register); all instances
  can serve *reads*. This avoids two instances assigning conflicting IDs. Election
  is coordinated via Kafka.

> 🧊 **In plain terms:** the catalogue keeps its *own* records as just another
> Kafka topic — it eats its own dog food. Restart it and it rebuilds the whole
> catalogue by replaying that topic.

---

## 6. Compatibility enforcement (the guardrail)

When you register a new schema to a subject, the registry checks it against the
subject's compatibility rule **and refuses it if it would break**. That turns a
would-be 3 a.m. production incident into a **deploy-time error**.

Quick recap (full detail in [Schema Evolution](schema-evolution-and-contracts.md)):

| Level | New schema must be readable by… | Upgrade order |
|---|---|---|
| `BACKWARD` (default) | consumers using the previous schema | consumers first |
| `FORWARD` | consumers using the *new* schema reading old data | producers first |
| `FULL` | both | either order |
| `NONE` | (no checks) | you're on your own |
| `*_TRANSITIVE` | checked against **all** prior versions, not just the last | strongest |

Ledgerstream uses **BACKWARD**. The habit that keeps you compatible: **add new
fields with defaults; never rename or remove required fields.**

---

## 7. Subject naming strategies

How a message maps to a subject determines what "a schema can evolve
independently of" means. Three strategies:

- **TopicNameStrategy (default):** subject = `<topic>-value` (and `-key`). One
  schema type per topic. Simple; most common.
- **RecordNameStrategy:** subject = the record's fully-qualified name. Lets a
  topic carry **multiple different event types**, each evolving on its own.
- **TopicRecordNameStrategy:** subject = `<topic>-<recordName>`. Multiple event
  types per topic **and** scoped per topic.

> Choose RecordName/TopicRecordName when you deliberately put several event types
> on one topic (e.g. an "order events" topic carrying `OrderCreated`,
> `OrderShipped`). Otherwise the default is right.

---

## 8. The REST API (what clients actually call)

The registry is an HTTP service. The endpoints you'll meet:

```
 POST /subjects/{subject}/versions          register a new schema (returns id)
 GET  /schemas/ids/{id}                      fetch a schema by global id
 GET  /subjects/{subject}/versions           list versions
 GET  /subjects/{subject}/versions/latest    latest schema for a subject
 POST /compatibility/subjects/{subject}/versions/latest
                                             test if a schema WOULD be compatible
 PUT  /config/{subject}                       set a subject's compatibility level
 GET  /config                                 get the global compatibility level
 GET  /subjects                               list all subjects
```

You rarely call these by hand — the Avro serializer/deserializer does it for you —
but they're how you *inspect* the registry (e.g. `GET /subjects` returns `[]` on a
fresh stack, which is why our health check hits `/subjects`).

---

## 9. Format support: Avro, Protobuf, JSON Schema

Modern registries support three serialization formats:
- **Avro** — the original/most common with Kafka; compact, rich evolution rules,
  schema resolution. **Ledgerstream's choice.**
- **Protobuf** — compact, field-number-based evolution, great cross-language via
  codegen.
- **JSON Schema** — human-readable, larger, looser evolution.

All three use the **same 5-byte wire header** (magic + ID) and the same
compatibility machinery; only the payload encoding differs.

---

## 10. Production concerns

- **High availability:** run multiple instances behind a load balancer; one is
  the write-leader (schema registration), all serve reads. Its state is the
  replicated `_schemas` topic.
- **Security:** in prod, lock it down — HTTPS, authentication (Basic/OAuth), and
  **restrict who can register** schemas (a rogue or buggy producer registering a
  bad schema is a real risk). Local dev is plaintext + open, called out as a
  simplification.
- **`_schemas` topic care:** it must stay log-compacted and should not be deleted
  — losing it loses the ID→schema mapping. Back it up / replicate it like the
  critical state it is.
- **Client caching + startup:** clients cache schemas, but a cold consumer needs
  the registry reachable to resolve an unseen ID — factor the registry into your
  readiness checks and network reachability.

---

## 11. Alternatives

- **Confluent Schema Registry** — the de-facto standard, Avro/Protobuf/JSON,
  `_schemas`-topic backed. (What we run.)
- **Apicurio Registry** (Red Hat) — open-source, broader artifact types, multiple
  storage backends, Confluent-API-compatible mode.
- **AWS Glue Schema Registry** — managed, integrates with AWS/MSK.
- **Karapace** (Aiven) — open-source, drop-in Confluent-API-compatible.

They interoperate because they share the wire format + REST API conventions.

---

## 12. Interview questions you should be able to answer

- *What problem does a Schema Registry solve?* → Lets independently deployed
  producers/consumers agree on message structure and evolve it safely, without
  shipping full schemas in every message or coordinating big-bang upgrades.
- *What's actually in a Kafka message when you use a registry?* → A 5-byte header
  (1 magic byte + 4-byte **global schema ID**) followed by the binary payload —
  not the schema itself.
- *Schema ID vs version?* → ID is global/unique per distinct schema and is what's
  embedded in messages; version is per-subject (1,2,3…) and human-facing.
- *What's a subject?* → A named evolution scope (default `<topic>-value`) whose
  compatibility rule governs allowed changes.
- *How does the producer/consumer use the registry at runtime?* → Serializer
  registers/looks-up the ID (cached) and prefixes it; deserializer reads the ID and
  fetches the writer schema (cached) to decode — registry is off the hot path.
- *Where does the registry store its schemas?* → In Kafka, in a compacted
  `_schemas` topic; instances rebuild an in-memory index by replaying it.
- *What is writer-vs-reader schema resolution?* → Avro reconciles the schema the
  data was written with against the schema the consumer expects (defaults for new
  fields, skip removed ones) — the mechanism behind compatibility at decode time.
- *How does it prevent breaking changes?* → It checks a new schema against the
  subject's compatibility level at **registration** and rejects incompatible ones.
- *Registry down — does streaming stop?* → No for already-cached schemas; you only
  lose the ability to register or resolve *new/unseen* schemas.
- *Why not just put the schema in every message?* → Defeats the point of compact
  binary formats; the ID is 4 bytes vs a whole schema.

---

## 13. How Ledgerstream uses it

- **Confluent Schema Registry**, run as a local Docker container
  (`docker-compose.yml`), reachable at `http://localhost:8081`; its health check
  hits `GET /subjects`.
- **Backed by Kafka** — it stores schemas in the `_schemas` topic on our local
  broker, so the registry and Kafka share a lifecycle.
- **Avro schemas** for `payments.events` / `ledger.events`
  (`PaymentCaptured`, `LedgerPosted`, …), registered under the default
  **TopicNameStrategy** subjects (`payments.events-value`, etc.).
- **Compatibility = BACKWARD** (set via `SCHEMA_REGISTRY_SCHEMA_COMPATIBILITY_LEVEL`)
  so consumers can be upgraded first and always read older messages; new fields
  ship with defaults, no renames.
- **Producers/consumers (Phase 2)** use Avro serializers/deserializers that embed
  the 5-byte header and cache schema lookups.
- **Production path:** the same Avro schemas move to a managed registry (Confluent
  Cloud SR / AWS Glue) with HTTPS + auth + restricted registration — a config
  change, not a code change (see [`docs/cloud-free-tiers.md §5`](../cloud-free-tiers.md)).

---

*Related: [Kafka deep-dive](kafka.md) · [Schema Evolution & Contracts](schema-evolution-and-contracts.md)
(the compatibility rules in depth) · [`docs/docker-compose-explained.md`](../docker-compose-explained.md)
(the registry container + its health check).*
