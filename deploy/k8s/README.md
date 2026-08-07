# `deploy/k8s/` — plain per-service manifests (readable alternative to the Helm chart)

These are the **same Kubernetes objects the Helm chart produces**, but written out as
**static YAML, one file per service**, with no templating — so you can read exactly what each
service gets. They're a learning/reference view and a second way to deploy.

> **Use EITHER the Helm chart OR these files — not both.** They create objects with the same
> names in the same namespace, so applying both just fights over the same resources. The Helm
> chart ([`../helm/ledgerstream`](../helm/ledgerstream)) is the "real" packaging (one release,
> easy upgrade/rollback); these files are the expanded, human-readable equivalent.

## Files

| File | What's in it |
|---|---|
| [`00-config.yaml`](00-config.yaml) | Namespace + shared **ConfigMap** (non-secret env, cross-service URLs) + **Secret** (placeholders) |
| [`payment.yaml`](payment.yaml) | payment **web** + **outbox-relay** + **ledger-outcomes** Deployments, and the payment Service |
| [`ledger.yaml`](ledger.yaml) | ledger **web** + **consume-payments** Deployments, and the ledger Service |
| [`gateway.yaml`](gateway.yaml) | gateway **web** Deployment + Service (stateless; no workers) |
| [`ai.yaml`](ai.yaml) | ai **web** Deployment + Service (FastAPI; extra LLM env) |
| [`ingress.yaml`](ingress.yaml) | **OPTIONAL** external HTTP entry (gateway `/`, ai `/ai`). Needs an ingress controller — skip it and use `port-forward` locally. |
| [`kafka.yaml`](kafka.yaml) | **OPTIONAL** Kafka + Schema Registry *in the cluster* (single-node KRaft, demo-grade). Apply it and the worker pods connect with no config change — the whole event pipeline runs in k8s. |
| [`kafka-ui.yaml`](kafka-ui.yaml) | **OPTIONAL** Kafka UI *in the cluster* (browse topics/messages/lag/schemas). Only useful with `kafka.yaml` — a host Kafka UI can't reach the in-cluster broker. |

Total: 7 Deployments + 4 Services + 1 ConfigMap + 1 Namespace (Secret created via CLI, see
below) — the same workload objects as the chart, plus the optional Ingress, in-cluster Kafka,
and Kafka UI.

## Apply

```powershell
# 1. Namespace + ConfigMap (this file has NO Secret — a plain manifest can't read env vars).
kubectl apply -f deploy/k8s/00-config.yaml

# 2. Create the Secret from your variables (set $JWT/$DJ/$PAY_DB/$LED_DB/$REDIS first).
#    This is how you feed secrets from the environment — via the CLI, not the YAML.
kubectl -n ledgerstream create secret generic ledgerstream-secrets --from-literal=JWT_SIGNING_KEY="$JWT" --from-literal=DJANGO_SECRET_KEY="$DJ" --from-literal=PAYMENT_DATABASE_URL="$PAY_DB" --from-literal=LEDGER_DATABASE_URL="$LED_DB" --from-literal=REDIS_URL="$REDIS" --dry-run=client -o yaml | kubectl apply -f -

# 3. Each service (AFTER the Secret exists — pods read env at startup).
kubectl apply -f deploy/k8s/payment.yaml
kubectl apply -f deploy/k8s/ledger.yaml
kubectl apply -f deploy/k8s/gateway.yaml
kubectl apply -f deploy/k8s/ai.yaml

# 4. Watch + reach the gateway.
kubectl -n ledgerstream get pods
kubectl -n ledgerstream port-forward svc/ledgerstream-gateway 8010:8010
```

### Optional add-ons

Run the **whole event pipeline in-cluster** (so the worker pods aren't crash-looping on Kafka),
plus a Kafka UI to watch it:
```powershell
# In-cluster Kafka + Schema Registry (creates the `kafka`/`schema-registry` Services).
kubectl apply -f deploy/k8s/kafka.yaml
```
```powershell
# Kafka UI (browse topics/messages/lag). View it via port-forward → http://localhost:8085
kubectl apply -f deploy/k8s/kafka-ui.yaml
kubectl -n ledgerstream port-forward svc/kafka-ui 8085:8080
```
```powershell
# If you'd scaled the workers to 0, bring them back so they consume/produce.
kubectl -n ledgerstream scale deployment ledgerstream-ledger-consume-payments ledgerstream-payment-outbox-relay ledgerstream-payment-ledger-outcomes --replicas=1
```

Expose the API on a real host instead of `port-forward`:
```powershell
kubectl apply -f deploy/k8s/ingress.yaml   # needs an ingress controller — see the file's header
```

Remove everything (add the optional files you applied):
```powershell
kubectl delete -f deploy/k8s/kafka-ui.yaml -f deploy/k8s/kafka.yaml -f deploy/k8s/ai.yaml -f deploy/k8s/gateway.yaml -f deploy/k8s/ledger.yaml -f deploy/k8s/payment.yaml -f deploy/k8s/00-config.yaml
```

## How to read one file

Every service file follows the same shape:
- **Deployment(s)** — "run N copies of this container." The `web` one exposes a port + health
  probes; each **worker** one instead overrides the `command:` and has no port/Service.
- **Service** — a stable name (`ledgerstream-<svc>`) that load-balances to that service's
  **`-web`** pods (matched by the `component: <svc>-web` label). Workers have no Service.
- Config comes in via **`envFrom`** — every pod loads the shared ConfigMap + Secret.

Assumes images tagged `docker.io/library/ledgerstream-<svc>:dev` (what `docker build -t
"ledgerstream-<svc>:dev"` produces, used with Docker Desktop's Kubernetes). Change the `image:`
lines for GHCR (`ghcr.io/<owner>/ledgerstream-<svc>:latest`).

## Caveats (same as the chart)
- **Workers need Kafka.** Default `KAFKA_BOOTSTRAP_SERVERS` is `kafka:9092`. Two ways to satisfy it:
  1. **Run Kafka in-cluster (simplest):** `kubectl apply -f deploy/k8s/kafka.yaml` — creates the
     `kafka` + `schema-registry` Services the pods already point at. Whole pipeline runs in k8s.
  2. **Use your host's docker-compose Kafka:** set `KAFKA_BOOTSTRAP_SERVERS=host.docker.internal:29092`
     + `SCHEMA_REGISTRY_URL=http://host.docker.internal:8081` in `00-config.yaml` — but this also
     needs the compose broker to *advertise* `host.docker.internal:29092`, so option 1 is easier.
- **No migration Job** — the DBs (Neon) must already be migrated.
