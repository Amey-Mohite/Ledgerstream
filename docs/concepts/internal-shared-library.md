# Building an Internal Shared Library (Python packaging for a monorepo)

> **In one sentence:** instead of copy-pasting the same logging/config/tracing code
> into every service, you put it in **one small installable Python package** that
> lives in the same repo, and each service installs it like any other dependency —
> so there's exactly one copy of that code to maintain, and every service is on the
> same version.

> 🧊 **In plain terms:** it's a **shared toolbox** in a workshop with several rooms
> (services). Rather than buy a hammer and tape measure for every room — which drift
> out of sync and confuse everyone — you keep **one standardized toolbox** that every
> room borrows from. This doc is *how that toolbox is built*: the box itself, the
> label on it, and how each room gets a copy.

> This is the **creation** guide (the folder, the files, the packaging). For how the
> library is *deployed* (baked into each service image, not run on its own), see
> [phase0.md Part 5](../phase0.md).

---

## 1. The problem it solves — and what belongs in it

Every service needs the same **cross-cutting plumbing**: structured JSON logging,
correlation-id propagation, OpenTelemetry tracing, Prometheus metrics, typed env
config. Three ways to share that:

| Option | Problem |
|---|---|
| **Copy-paste** into each service | Drifts instantly; a fix in one service never reaches the others. |
| **Git submodule** | Fiddly, easy to get on the wrong commit, poor tooling. |
| **An installed package** (what we do) | One source of truth; services depend on it like any library. |

The hard rule is **what does NOT go in**:

- ✅ **In:** framework-neutral primitives — logging, config, correlation, metrics,
  tracing. Generic, no business rules.
- ❌ **Out:** business logic (payments, ledger) and **framework glue** (no `import
  django`, no `import fastapi`). If the shared lib imported Django, pulling it into
  the FastAPI service would drag Django in too. Keeping it framework-free is what
  makes it safe to depend on everywhere.

> The correlation-id *middleware* (which reads an HTTP header) lives in each service,
> because middleware is framework-specific. The shared lib only provides the
> framework-neutral core (`contextvars`), and each service wires its framework to it.

---

## 2. Anatomy of the folder — what each file is

```
libs/shared/
├── pyproject.toml            ← the package manifest (name, deps, build config)
├── README.md                 ← how to install & use it
├── ledgerstream_shared/      ← THE PACKAGE (the importable code)
│   ├── __init__.py           ← marks it a package + declares the public surface
│   ├── config.py             ← typed env access (require_env, get_int, …)
│   ├── correlation.py        ← request-scoped correlation id via contextvars
│   ├── logging.py            ← structured JSON logs + correlation-id injection
│   ├── metrics.py            ← Prometheus registry + metric factories
│   ├── tracing.py            ← OpenTelemetry tracer (OTLP → collector)
│   └── kafka.py              ← Kafka/Schema-Registry helpers (optional, see §4)
└── tests/                    ← unit tests for the primitives
    ├── test_correlation.py
    ├── test_logging.py
    └── test_retry.py
```

Two names that look the same but aren't:

- **Distribution name** = `ledgerstream-shared` (with a hyphen). This is what you
  `pip install`, what PyPI would list. Set in `pyproject.toml`.
- **Import name** = `ledgerstream_shared` (with an underscore). This is the **folder**
  and what you write in code: `from ledgerstream_shared.logging import …`.

They differ because pip/PyPI allow hyphens but Python identifiers can't. A package can
even ship several import names; here it's one, matching the folder.

### `__init__.py` — why the empty-ish file matters

A directory becomes an importable **package** only if it has an `__init__.py`. Ours
also declares the **public surface** so `import ledgerstream_shared` gives you the
submodules and a version:

```python
from ledgerstream_shared import config, correlation, logging, metrics, tracing
__all__ = ["config", "correlation", "logging", "metrics", "tracing"]
__version__ = "0.1.0"
```

---

## 3. `pyproject.toml`, section by section

`pyproject.toml` is the modern, standardized manifest (PEP 517/518/621) that replaced
`setup.py`. Ours:

```toml
[build-system]                                  # HOW to build the package
requires = ["setuptools>=68", "wheel"]          # tools needed to build it
build-backend = "setuptools.build_meta"         # which builder pip should call

[project]                                       # WHAT the package is (metadata)
name = "ledgerstream-shared"                    # the distribution name (pip install this)
version = "0.1.0"
description = "Cross-service primitives …"
requires-python = ">=3.11"
dependencies = [                                # ALWAYS-installed runtime deps
    "opentelemetry-api>=1.27.0",
    "opentelemetry-sdk>=1.27.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.27.0",
    "prometheus-client>=0.20.0",
]

[project.optional-dependencies]                 # EXTRAS — installed only if asked (§4)
dev  = ["pytest>=8.0.0"]
kafka = ["confluent-kafka[avro]>=2.5", "fastavro>=1.9"]

[tool.setuptools.packages.find]                 # WHICH folders are the package
where = ["."]
include = ["ledgerstream_shared*"]              # ship ledgerstream_shared, ignore tests/

[tool.pytest.ini_options]                       # test config lives here too
testpaths = ["tests"]
```

What each block does, in plain terms:

- **`[build-system]`** — tells pip *how to build*: use setuptools as the backend. You
  need this even for a pure-Python package; it's the contract pip relies on.
- **`[project]`** — the identity card: name, version, the Python it needs, and the
  **dependencies every install pulls in** (OTel + Prometheus — the observability core
  every service uses).
- **`[project.optional-dependencies]`** — named bundles of *extra* deps you opt into
  (see §4).
- **`[tool.setuptools.packages.find]`** — auto-discovers packages to ship. `include =
  ["ledgerstream_shared*"]` means the `tests/` folder is **not** packaged (tests ship
  with the repo, not inside the installed library).
- **`[tool.pytest.ini_options]`** — pytest reads its own config from here, so running
  `pytest` in `libs/shared` just finds `tests/`.

---

## 4. Extras: why Kafka is *optional*

Not every service touches Kafka. The AI Query service (Phase 6) never produces or
consumes events, so it shouldn't be forced to install `confluent-kafka` (which pulls
in the native `librdkafka` C library). So Kafka deps live in an **extra**:

```toml
kafka = ["confluent-kafka[avro]>=2.5", "fastavro>=1.9"]
```

- A Kafka service installs `ledgerstream-shared[kafka]` → gets the core **plus** the
  Kafka deps.
- A non-Kafka service installs `ledgerstream-shared` → gets only the core; `kafka.py`
  is still present but its `confluent_kafka` import would fail if used — which is fine,
  because that service never imports it.

This is why `libs/shared/ledgerstream_shared/kafka.py` opens with a docstring saying
"requires the `kafka` extra" — the module is shipped always, but its dependencies are
opt-in. Extras are the standard way to keep a shared library lightweight for the
consumers that only need part of it.

---

## 5. Editable install vs regular install (the `-e` you keep seeing)

Two ways to install the library, for two different situations:

| Command | What it does | Use it for |
|---|---|---|
| `pip install -e libs/shared` | **Editable**: site-packages gets a *pointer* to the source folder. Edit a `.py` and the change is live immediately — no reinstall. | **Local dev** — you're editing the lib and the service together. |
| `pip install libs/shared` | **Regular**: builds the package and **copies a snapshot** into site-packages. Later edits to the source are *not* seen until you reinstall. | **Docker images** — you want a frozen copy baked in, reproducible. |

That's the whole reason the [Dockerfile](../../services/payment/Dockerfile) uses
`RUN pip install /app/libs/shared` (frozen snapshot in the image) while the
[Makefile](../../Makefile) and README use `pip install -e libs/shared[dev]` (live
editing during development). Same package, two install modes.

> **`-e` = "editable" (a.k.a. development install).** Think symlink, not copy: pip
> records "this package's code is over there" instead of duplicating it. Essential in
> a monorepo where you change the lib and its consumers in the same edit.

---

## 6. How a service consumes it — the three touch points

A service depends on the shared lib in three places, each for a different audience:

1. **`requirements.txt`** — a *comment*, not a line, because it's installed separately
   (editable in dev, or by the Dockerfile). It documents the dependency:
   ```
   # Shared observability library (installed separately, editable):
   #   pip install -e ../../libs/shared
   ```
2. **`Dockerfile`** — copies the lib into the build context and installs a frozen
   snapshot **before** the service code, so this rarely-changing layer caches well:
   ```dockerfile
   COPY libs/shared /app/libs/shared
   RUN pip install /app/libs/shared
   ```
   (The image must be **built from the repo root** so `libs/shared` is in the build
   context — noted at the top of each Dockerfile.)
3. **`Makefile`** — a one-liner for local setup: `pip install -e libs/shared[dev]`.

Then service code just imports it like any package:

```python
from ledgerstream_shared.logging import configure_logging
from ledgerstream_shared.tracing import configure_tracing

configure_logging("payment-service", level="INFO")
configure_tracing("payment-service")
```

---

## 7. Recipe — adding a new module or helper

1. Create `ledgerstream_shared/<name>.py` with framework-neutral code.
2. If it needs a new third-party dep, add it to `[project.dependencies]` (always
   installed) or a relevant extra (opt-in). Bump `version` if consumers must notice.
3. Export it from `__init__.py` if it's part of the public surface.
4. Add a test under `tests/` (the `run_with_retry` helper, for example, got
   `tests/test_retry.py`).
5. In editable mode there's **nothing to reinstall** — the change is live. For images,
   the next `docker build` bakes it in.

That's exactly how Phase 3 added `run_with_retry` to `kafka.py`: write the function,
add a test, done — both consumers picked it up with no packaging changes because the
dependency (`confluent-kafka`) was already in the `kafka` extra.

---

## 8. Interview questions you should be able to answer

- *Why a shared library instead of copy-paste or a git submodule?* → One source of
  truth, normal dependency tooling, all services on the same version; submodules are
  fiddly and copy-paste drifts.
- *Distribution name vs import name?* → `pip install` uses the distribution name
  (hyphens allowed); `import` uses the package/folder name (must be a valid Python
  identifier, so underscores).
- *What's `pyproject.toml` and why not `setup.py`?* → The standardized PEP 517/621
  build manifest; declarative, tool-agnostic, replaces imperative `setup.py`.
- *What are extras and when do you use them?* → Named optional dependency bundles
  (`[kafka]`); keep the base install light and let consumers opt into heavy deps they
  need (librdkafka) without forcing them on everyone.
- *Editable (`-e`) vs regular install?* → Editable points at the source (live edits,
  for dev); regular copies a built snapshot (frozen, for images/CI).
- *Why must the shared lib import no web framework?* → So pulling it into one service
  never drags in another's framework; it stays a neutral, universally-safe dependency.
- *How is it deployed — is it a running service?* → No. It's baked into each service
  image at build time; N services → N copies, no separate container. (See phase0.)
- *Monorepo build-time install vs a published private package — trade-off?* → Monorepo:
  simple, lockstep versions, rebuild-all to update. Published: services upgrade
  independently, but needs a private registry + version pinning.

---

## 9. How Ledgerstream uses it

`libs/shared` is the **one** explicit dependency every service shares:
`ledgerstream_shared` with modules `config`, `correlation`, `logging`, `metrics`,
`tracing`, and `kafka` (the last behind the `[kafka]` extra). It carries **no business
logic and no framework imports**, so the Django services and the FastAPI AI service
can all depend on it without contaminating each other. Services install it **editable**
in local dev (`make setup`) and as a **frozen snapshot** in their Docker images
(`COPY libs/shared` + `pip install`), so the library exists as one source in the repo
and one baked-in copy per image — never as a running service. Built in **Phase 0**;
extended in later phases (e.g. `run_with_retry` in Phase 3). Deployment topology:
[phase0.md Part 5](../phase0.md).
