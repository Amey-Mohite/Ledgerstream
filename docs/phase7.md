# Phase 7 — Deployment: Containers → k8s/Helm → Terraform → CI, from scratch

> **What Phase 7 adds:** no new features — instead, the road from *"it runs on my
> laptop"* to *"it runs in a cluster, and a robot checks every commit."* Four pieces:
> **CI** (GitHub Actions), a **Helm chart** (how the services run in Kubernetes),
> **Terraform** (one command to stand it all up), and the **container** story that ties
> them together.
>
> Read the four concept docs alongside this — the theory:
> [containers-and-images](concepts/containers-and-images.md),
> [kubernetes-and-helm](concepts/kubernetes-and-helm.md),
> [ci-cd-pipelines](concepts/ci-cd-pipelines.md),
> [infrastructure-as-code](concepts/infrastructure-as-code.md).

**Honesty up front (tiering):** the **CI pipeline is Tier 1** — it runs on every commit,
runs the real test suites, and builds the images; it catches breakage for real. The
**Helm chart and Terraform are Tier 3 (showcase)** — they're validated (`helm lint`,
`helm template`, `terraform validate`) and a `kind` cluster runs them locally, but this
phase does **not** apply them to a live paid cluster. That's the honest, interview-ready
framing: I can deploy this, and here's exactly how — I just didn't rent a cluster to do it.

---

## Part 1 — The big picture: from image to cluster

```mermaid
flowchart LR
  subgraph dev["Your commit"]
    A["git push"]
  end
  subgraph ci["GitHub Actions (CI)"]
    B["run tests<br/>(shared, django+pg, hermetic)"] --> C["build 4 images"]
    C -->|"on main"| D["push to GHCR"]
  end
  subgraph cluster["Kubernetes cluster"]
    E["Terraform apply"] --> F["Helm release"]
    F --> G["Deployments + Services<br/>payment · ledger · gateway · ai (+ workers)"]
    G -.->|"pulls images"| D
  end
  subgraph ext["External managed data (inputs)"]
    H["Neon · Upstash · Atlas · Kafka/SR"]
  end
  A --> B
  G -.->|"URLs via Secret/ConfigMap"| H
```

The chain: **push** → CI **tests + builds + publishes** images → **Terraform** installs the
**Helm** chart → Helm creates the **Deployments/Services** → pods **pull** the published
images and read their config/secrets. The **data stores are external** — the cluster holds
only the stateless app tier and reaches Postgres/Redis/Mongo/Kafka by URL.

---

## Part 2 — Containers: one image, many run modes

You already have four Dockerfiles ([services/*/Dockerfile](../services/payment/Dockerfile)).
The Phase 7 insight is *how one image becomes several running things* in k8s.

Look at the **ledger** image's own comment — it spells out the pattern:

```dockerfile
# ONE image, TWO run modes (different command per Kubernetes Deployment):
#   web      → gunicorn (the read API)          — the default CMD below
#   consumer → python manage.py consume_payments — the Kafka worker
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", ...]
```

- The image's **default `CMD`** is the **web** process (gunicorn/uvicorn).
- A **worker** is the *same image* with the `CMD` **overridden** by a `command:` in its
  Deployment (`python manage.py consume_payments`).

So `ledgerstream-payment:latest` runs as the payment API **and** as the outbox relay
**and** as the ledger-outcomes consumer — three Deployments, one image. That's why we build
only four images but run seven Deployments. Full theory:
[containers-and-images.md](concepts/containers-and-images.md).

Two details worth knowing (they're in the Dockerfiles):
- **Build context is the repo root** (`docker build -f services/payment/Dockerfile .`) —
  because each image needs `libs/shared`, which lives *outside* the service directory.
- **Non-root user** (`USER appuser`) — least privilege; a compromised process isn't root.

---

## Part 3 — The Helm chart, file by file

[deploy/helm/ledgerstream/](../deploy/helm/ledgerstream/). The whole point: **one chart
renders all four services** (and their workers) by ranging over a map — no copy-paste.

### The variability surface — `values.yaml`

Everything that differs between services lives in one `services:` map:

```yaml
services:
  payment:
    port: 8000
    workloads:
      - name: web                                             # no command → web process
        replicas: 2
      - name: outbox-relay
        command: ["python", "manage.py", "run_outbox_relay"]  # command → worker
        replicas: 1
      - name: ledger-outcomes
        command: ["python", "manage.py", "consume_ledger_outcomes"]
        replicas: 1
  ledger:   { port: 8000, workloads: [web, consume-payments] }
  gateway:  { port: 8010, workloads: [web] }
  ai:       { port: 8030, workloads: [web], env: { LLM_PROVIDER_ORDER: "...", ... } }
```

Plus three env buckets: `commonEnv` (non-secret, all pods), `externalEnv` (Kafka/SR
endpoints), and `secretEnv` (placeholders — real values at install time).

### The generic Deployment — `templates/deployment.yaml`

This is the heart. Two nested loops turn the map above into k8s objects:

```
range service (payment, ledger, gateway, ai)
  range workload (web, outbox-relay, ...)
    → one Deployment
      • image  = {registry}/{prefix}-{serviceName}:{tag}
      • if workload has a `command` → set it (worker); else expose port + TCP probes (web)
      • envFrom the shared ConfigMap + Secret
```

So a web workload gets a `containerPort` + readiness/liveness probes; a worker gets its
`command:` and **no** port or Service (nothing connects *to* a worker — it pulls from Kafka).

### Cross-service addressing — `templates/configmap.yaml`

gateway needs to reach payment/ledger; ai needs to reach gateway. Instead of hardcoding,
the ConfigMap **computes** the in-cluster URLs from the release name:

```yaml
PAYMENT_BASE_URL: "http://{{ include "ledgerstream.fullname" . }}-payment:8000"
GATEWAY_BASE_URL: "http://{{ include "ledgerstream.fullname" . }}-gateway:8010"
```

k8s DNS resolves `ledgerstream-payment` to that Service's cluster IP. These **override**
the `localhost:...` defaults baked into each `settings.py`, so the same code that ran
natively now finds its neighbours in the cluster — no code change.

### The rest

- `templates/service.yaml` — one `ClusterIP` Service per service, selecting only its
  **`-web`** pods.
- `templates/secret.yaml` — one `Secret` from `secretEnv` (placeholders; real values via
  `--set-string`).
- `templates/ingress.yaml` — optional external entry (off by default; dev uses
  `port-forward`).
- `templates/_helpers.tpl` — name/label helpers (standard Helm boilerplate).
- `templates/NOTES.txt` — the "what now?" printed after install (port-forward commands, a
  warning if `JWT_SIGNING_KEY` is still empty).

**Validated:**
```bash
helm lint deploy/helm/ledgerstream
helm template ls deploy/helm/ledgerstream --set-string secretEnv.JWT_SIGNING_KEY=dummy
# → 13 objects: 1 Secret + 1 ConfigMap + 4 Services + 7 Deployments
```

Full theory: [kubernetes-and-helm.md](concepts/kubernetes-and-helm.md).

---

## Part 3.5 — `_helpers.tpl` explained (named templates)

[templates/_helpers.tpl](../deploy/helm/ledgerstream/templates/_helpers.tpl) is the chart's
**"functions" file** — reusable snippets of text the other templates call, so names and
labels are written **once** and stay consistent across all 13 objects. This is the part of
Helm that reads least like YAML, so here it is from zero.

### The problem it solves
Without helpers you'd copy the same label block into `deployment.yaml`, `service.yaml`,
`configmap.yaml`, `secret.yaml`… Change one label → edit five files and hope you didn't miss
one. Helm lets you **define a snippet once and `include` it** wherever needed. `_helpers.tpl`
is where those definitions live.

**Two rules of the file:**
1. The leading **`_`** in the filename means "this file renders **no** Kubernetes object on
   its own." Normal templates become YAML; `_helpers.tpl` only *defines* snippets for others.
2. Create a snippet with `{{- define "name" -}} … {{- end -}}`; use it with
   `{{ include "name" <context> }}`. Think `define` = *write a function*, `include` = *call it*.

### The `include ... .` context
In `{{ include "ledgerstream.labels" . }}`, the trailing **`.`** is the **data you hand the
function** — the root object with `.Values`, `.Release`, `.Chart`. A snippet can only see what
you pass it, which is why the templates pass **`$root`** (their saved copy of `.`, taken
*before* a `range` loop reassigns `.` to the loop item) so the helper can still reach
`.Release.Name`, `.Chart.Name`, etc.

Assume below: release **`ledgerstream`**, chart name **`ledgerstream`**, chart version
**`0.1.0`**, appVersion **`phase7`**.

### 1. `ledgerstream.name` — the short name
```gotemplate
{{- define "ledgerstream.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}
```
Read the pipes (`|` = "feed the result into the next function") left to right:
- `default .Chart.Name .Values.nameOverride` → use `nameOverride` if set, else the chart name → `ledgerstream`
- `| trunc 63` → cut to 63 chars (k8s label values legally max out at 63)
- `| trimSuffix "-"` → drop a trailing `-` if truncation left one (labels can't end in `-`)

**Output:** `ledgerstream`.

### 2. `ledgerstream.fullname` — the object-name prefix
```gotemplate
{{- define "ledgerstream.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}
```
Builds the **prefix every object name starts with** (`ledgerstream-payment`, `ledgerstream-config`…). It's an if/else:
- `fullnameOverride` set → use it, done.
- Else `$name` = chart name. **If the release name already contains it** (`contains
  "ledgerstream" "ledgerstream"` → true) → use just the release name → `ledgerstream` (avoids
  an ugly doubled `ledgerstream-ledgerstream`). **Else** glue them:
  `printf "%s-%s" .Release.Name $name` → release `myrel` → `myrel-ledgerstream`.

**Output (release `ledgerstream`):** `ledgerstream` — which is why `service.yaml` yields
`ledgerstream-payment` and the ConfigMap's `http://ledgerstream-payment:8000` resolves.
(`printf "%s-%s"` is string formatting: each `%s` = "put a string here".)

### 3. `ledgerstream.labels` — the full label set (stamped on every object)
```gotemplate
{{- define "ledgerstream.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "ledgerstream.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
```
Note it **calls another helper** (`include "ledgerstream.selectorLabels"`) — helpers can use
helpers. Renders to:
```yaml
helm.sh/chart: ledgerstream-0.1.0
app.kubernetes.io/name: ledgerstream
app.kubernetes.io/instance: ledgerstream
app.kubernetes.io/managed-by: Helm
app.kubernetes.io/version: "phase7"
```
- `replace "+" "_"` — chart versions can contain `+` (build metadata), illegal in a label →
  swap for `_`.
- `.Release.Service` → `Helm`. `| quote` wraps a value in quotes so `"phase7"` is a valid string.

These are the k8s **"recommended labels"** — a convention so tools/dashboards can tell what an
object is and what installed it.

### 4. `ledgerstream.selectorLabels` — the stable subset
```gotemplate
{{- define "ledgerstream.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ledgerstream.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
```
Renders:
```yaml
app.kubernetes.io/name: ledgerstream
app.kubernetes.io/instance: ledgerstream
```
**Why a separate, smaller set from `labels`?** These two are the **selector** that ties a
Deployment to its Pods and a Service to its Pods. A Deployment's selector is **immutable**
after creation. So it must contain only labels that **never change**. `labels` (#3) includes
`version: "phase7"` and the chart version — those change every release; if the selector
included them, the next `helm upgrade` would try to mutate an immutable field and **fail**. So:
`selectorLabels` = stable identity (for matching); `labels` = identity + descriptive extras
(for display/metadata).

### The whitespace dashes (`{{-` and `-}}`)
Every line uses `{{-` (trim whitespace/newline **before** the tag) and `-}}` (trim **after**).
Without them each `define` would emit stray blank lines and break the rendered YAML's
indentation. They just keep the output tight.

### The one mental model
`_helpers.tpl` = **write the name/label logic once**; the real templates call it with
`include "name" $root`. Change how everything is named/labeled → edit this one file and all 13
objects update together. That's why names and selectors can never drift apart — which is the
#1 way a Service silently stops routing (selector no longer matches the pod labels).

---

## Part 4 — Terraform: one command to stand it up

[deploy/terraform/](../deploy/terraform/). Terraform's job here is small and honest: own the
**namespace** and **install the Helm chart**, injecting secrets safely.

```hcl
# main.tf (trimmed)
resource "kubernetes_namespace" "ledgerstream" { metadata { name = var.namespace } }

resource "helm_release" "ledgerstream" {
  name      = var.release_name
  namespace = kubernetes_namespace.ledgerstream.metadata[0].name
  chart     = "${path.module}/../helm/ledgerstream"      # the chart from Part 3
  set { name = "image.registry" value = var.image_registry }
  set { name = "image.tag"      value = var.image_tag }
  dynamic "set_sensitive" {                              # each secret, kept out of logs
    for_each = var.secrets
    content { name = "secretEnv.${set_sensitive.key}", value = set_sensitive.value }
  }
}
```

- The **`kubernetes` + `helm` providers** both read your kubeconfig, so `terraform apply`
  works against `kind`, minikube, or a cloud cluster — same config.
- **`set_sensitive`** fans the `secrets` map into `--set-string secretEnv.<KEY>=...` so
  values never appear in plan output or state diffs.
- **State hygiene**: `.gitignore` blocks `*.tfstate` (it can contain secrets) and
  `*.tfvars` (real values) — only `terraform.tfvars.example` is committed.

**Validated:** `terraform fmt`, `terraform init -backend=false`, `terraform validate` → OK.
Full theory: [infrastructure-as-code.md](concepts/infrastructure-as-code.md).

---

## Part 5 — CI: the robot that checks every commit

[.github/workflows/ci.yml](../.github/workflows/ci.yml). Four jobs; the build waits on the
tests (`needs:`).

```mermaid
flowchart TD
  P["push / PR"] --> S["shared-tests"]
  P --> DB["django-db-tests<br/>(payment+ledger vs real Postgres)"]
  P --> H["hermetic-tests<br/>(gateway: fakeredis · ai: mock LLM)"]
  S --> B
  DB --> B
  H --> B["build-images (×4)"]
  B -->|"branch == main"| G["push to GHCR"]
```

The clever bit is **matching CI to how the tests already work**:

- **`django-db-tests`** spins up **two Postgres service containers** on ports **5433/5434** —
  exactly the ports the payment/ledger `conftest.py` files default to
  (`postgresql://payment:...@localhost:5433/payment`). So CI needs **zero test changes**;
  the suite finds its database. pytest-django creates the `test_*` DB and runs migrations.
- **`hermetic-tests`** (gateway, ai) need no infra — gateway swaps in `fakeredis`, ai forces
  `LLM_PROVIDER_ORDER=mock`. A matrix runs both in parallel.
- **`build-images`** builds all four Dockerfiles (context = repo root) on every commit to
  prove they still build, and **pushes to GHCR only on `main`** (login gated by branch;
  the owner is lowercased because GHCR requires it).

Full theory: [ci-cd-pipelines.md](concepts/ci-cd-pipelines.md).

---

## Part 6 — Run it yourself (local cluster)

Subsections: **6.1** get a cluster, then deploy either with **6.2** Helm or **6.3** raw
`kubectl` manifests (pick one); **6.4** optionally run Kafka in-cluster for the full event
pipeline. A shared troubleshooting table closes the part.

### 6.1 — Set up a local cluster

#### Why a cluster at all?

A **cluster is the thing that actually runs your containers** — Kubernetes doesn't run
anything without one; Kubernetes *is* the manager of a cluster. A cluster = worker machines
(**nodes**) + the Kubernetes brain (**control plane**) that schedules pods onto them, restarts
what dies, and wires up networking.

The Helm chart and Terraform only describe **what** to run (Deployments, Services, config);
they need **somewhere** to run it. That somewhere is the cluster:

```
Helm chart / Terraform  ──"install this"──►  a Kubernetes cluster  ──runs──►  your pods
```

In production the cluster is a fleet of cloud servers (EKS/GKE/AKS). On your laptop you don't
have that, so a **local cluster** stands in — the *exact same* manifests run against it, for
free. You create one purely to have a target to deploy into; swapping to a cloud cluster later
is just a different kubeconfig, no chart changes. (Don't want k8s at all? The services are
plain containers — Render/Railway/Fly.io/Cloud Run run them without a cluster. The cluster is
only for the "I can do Kubernetes" path.)

#### Which local cluster? kind, minikube, or Docker Desktop — all work

The chart/Terraform target whatever **kubeconfig context** is active, so any of these is fine.
The **only** difference is how you make your locally-built images visible to that cluster (its
pods can't see your Docker images automatically). The chart's default `imagePullPolicy:
IfNotPresent` + a non-`latest` tag (`:dev`) means k8s uses a local image and won't try to pull
from a registry.

> ⚠️ **Windows / PowerShell users:** the three option blocks below are **bash** (`for … do …
> done`). They will NOT run in PowerShell — jump straight to the **[Windows / PowerShell
> walkthrough](#windows--powershell-walkthrough-the-exact-steps-that-worked)** below, which
> uses `foreach (...) { ... }` and Docker Desktop's Kubernetes. The bash blocks here are for
> Git Bash / WSL / macOS / Linux.

**Option A — kind** (Kubernetes IN Docker):
```bash
kind create cluster --name ledgerstream
for s in payment ledger gateway ai; do
  docker build -f services/$s/Dockerfile -t ledgerstream-$s:dev .
  kind load docker-image ledgerstream-$s:dev --name ledgerstream   # ← makes images visible
done
kubectl config use-context kind-ledgerstream
```

**Option B — minikube:**
```bash
minikube start
for s in payment ledger gateway ai; do
  docker build -f services/$s/Dockerfile -t ledgerstream-$s:dev .
  minikube image load ledgerstream-$s:dev                          # ← makes images visible
done
kubectl config use-context minikube
```

**Option C — Docker Desktop's built-in Kubernetes** (Settings → Kubernetes → Enable):
Docker Desktop's cluster **shares your local image store**, so a plain `docker build` is
already visible — **no load step**. Just build:
```bash
for s in payment ledger gateway ai; do
  docker build -f services/$s/Dockerfile -t ledgerstream-$s:dev .
done
kubectl config use-context docker-desktop
```
PowerShell equivalent (note `${s}` — a bare `$s:dev` is parsed as a scoped variable):
```powershell
foreach ($s in "payment","ledger","gateway","ai") { docker build -f services/$s/Dockerfile -t "ledgerstream-${s}:dev" . }
kubectl config use-context docker-desktop
```

### 6.2 — Deploy with Helm

#### Install the chart (bash — macOS / Linux / Git Bash)

```bash
helm upgrade --install ledgerstream deploy/helm/ledgerstream \
  --namespace ledgerstream --create-namespace \
  --set image.registry=docker.io/library --set image.repositoryPrefix=ledgerstream --set image.tag=dev \
  --set-string secretEnv.JWT_SIGNING_KEY="$JWT" \
  --set-string secretEnv.DJANGO_SECRET_KEY="$DJ" \
  --set-string secretEnv.PAYMENT_DATABASE_URL="$PAY_DB" \
  --set-string secretEnv.LEDGER_DATABASE_URL="$LED_DB" \
  --set-string secretEnv.REDIS_URL="$REDIS" \
  --set-string secretEnv.ANTHROPIC_API_KEY="$ANTHROPIC" \
  --set-string secretEnv.OPENAI_API_KEY="$OPENAI"

# Reach the gateway
kubectl -n ledgerstream port-forward svc/ledgerstream-gateway 8010:8010
```

> **Note on `image.registry`:** locally-built images have no registry prefix, so point the
> chart at `docker.io/library` + tag `dev` (the image ref becomes
> `docker.io/library/ledgerstream-payment:dev`, which matches your local build). With GHCR, use
> your `ghcr.io/<owner>` and `latest`.

Or via **Terraform** (after images are visible to the cluster; set `kube_context` to
`kind-ledgerstream` / `minikube` / `docker-desktop`):
```bash
cd deploy/terraform
terraform init
TF_VAR_secrets='{"JWT_SIGNING_KEY":"...","PAYMENT_DATABASE_URL":"...", ...}' \
  terraform apply \
    -var image_registry=docker.io/library -var image_tag=dev \
    -var kube_context=kind-ledgerstream        # or minikube / docker-desktop
```

The backing stores (Postgres/Redis/Kafka) still need to be reachable from the pods — point
the URLs at your Neon/Upstash/Atlas, or run the compose stack and use host networking.

#### Windows / PowerShell walkthrough (the exact steps that worked)

The commands above are bash-flavoured. On Windows + PowerShell + **Docker Desktop's built-in
Kubernetes**, here's the exact sequence, including the tool installs.

**One-time setup:**

1. **Install Helm** (no package manager needed — it's a single .exe):
   ```powershell
   $dir = "$env:USERPROFILE\tools\helm"
   New-Item -ItemType Directory -Force -Path $dir | Out-Null
   Invoke-WebRequest -Uri "https://get.helm.sh/helm-v3.16.4-windows-amd64.zip" -OutFile "$dir\helm.zip"
   Expand-Archive -Path "$dir\helm.zip" -DestinationPath $dir -Force
   Copy-Item "$dir\windows-amd64\helm.exe" "$dir\helm.exe" -Force
   [Environment]::SetEnvironmentVariable("Path", $env:Path + ";$dir", "User")
   ```
   Then **open a new PowerShell** so PATH refreshes. (`kubectl` already ships with Docker Desktop.)

2. **Enable Docker Desktop's Kubernetes:** Settings ⚙ → Kubernetes → Enable → Apply & Restart.

3. **Verify the toolchain:**
   ```powershell
   helm version; kubectl config use-context docker-desktop; kubectl get nodes
   ```
   Nodes `Ready` = your cluster is live.

**Deploy (repeat on each redeploy):**

4. **Build the four images** (Docker Desktop shares its image store → no load step). Note the
   `${s}` braces — in PowerShell a bare `$s:dev` is parsed as a scoped variable and breaks the tag:
   ```powershell
   foreach ($s in "payment","ledger","gateway","ai") { docker build -f services/$s/Dockerfile -t "ledgerstream-${s}:dev" . }
   ```

5. **Set secrets from your `.env`** (reuse the same `JWT_SIGNING_KEY`; `DJANGO_SECRET_KEY` can be any string):
   ```powershell
   $JWT="paste-JWT_SIGNING_KEY"; $DJ="any-random-string"; $PAY_DB="paste-PAYMENT_DATABASE_URL"; $LED_DB="paste-LEDGER_DATABASE_URL"; $REDIS="paste-REDIS_URL"
   ```

6. **Install the chart** (one line — PowerShell has no `\` line-continuation):
   ```powershell
   helm upgrade --install ledgerstream deploy/helm/ledgerstream --namespace ledgerstream --create-namespace --set image.registry=docker.io/library --set image.repositoryPrefix=ledgerstream --set image.tag=dev --set-string secretEnv.JWT_SIGNING_KEY="$JWT" --set-string secretEnv.DJANGO_SECRET_KEY="$DJ" --set-string secretEnv.PAYMENT_DATABASE_URL="$PAY_DB" --set-string secretEnv.LEDGER_DATABASE_URL="$LED_DB" --set-string secretEnv.REDIS_URL="$REDIS"
   ```

7. **Watch the pods** until the `*-web` ones are `Running`:
   ```powershell
   kubectl -n ledgerstream get pods
   ```

8. **Reach the gateway** (leave this running; open a NEW terminal for step 9):
   ```powershell
   kubectl -n ledgerstream port-forward svc/ledgerstream-gateway 8010:8010
   ```

9. **Test it** (new terminal). Health first, then the full smoke flow via Git Bash:
   ```powershell
   curl.exe http://localhost:8010/health/ready
   ```
   ```bash
   GW=http://localhost:8010 bash scripts/smoke_gateway.sh
   ```

10. **Redeploy after a code/Dockerfile change** — rebuild with a **NEW tag** and point the
    Deployment(s) at it. (Reusing `:dev` + `rollout restart` does **not** work on Docker Desktop —
    the kubelet keeps the cached image; see the troubleshooting table.)
    ```powershell
    docker build -f services/ledger/Dockerfile -t "ledgerstream-ledger:v2" .
    kubectl -n ledgerstream set image deployment/ledgerstream-ledger-web deployment/ledgerstream-ledger-consume-payments ledger=ledgerstream-ledger:v2
    ```

11. **Uninstall when done:**
    ```powershell
    helm -n ledgerstream uninstall ledgerstream
    ```

### 6.3 — Deploy with raw manifests (`kubectl`, no Helm)

The same objects exist as static, per-service YAML in [`deploy/k8s/`](../deploy/k8s/) (one file
per service, heavily commented). Use these OR the Helm chart (6.2) — **not both** (same object names).

1. **If a Helm release is installed, remove it first** (avoids name clashes):
   ```powershell
   helm -n ledgerstream uninstall ledgerstream
   ```
2. **Set your real secret values** (if not already set this session):
   ```powershell
   $JWT="paste-JWT_SIGNING_KEY"; $DJ="any-random-string"; $PAY_DB="paste-PAYMENT_DATABASE_URL"; $LED_DB="paste-LEDGER_DATABASE_URL"; $REDIS="paste-REDIS_URL"
   ```
3. **Create the namespace + ConfigMap** (the file intentionally holds NO Secret — a plain
   manifest can't read env vars, so we never hardcode secrets in it):
   ```powershell
   kubectl apply -f deploy/k8s/00-config.yaml
   ```
4. **Create the Secret from your variables** (this is how you "use env vars" — from the CLI, not
   the YAML; keeps secrets out of the committed file):
   ```powershell
   kubectl -n ledgerstream create secret generic ledgerstream-secrets --from-literal=JWT_SIGNING_KEY="$JWT" --from-literal=DJANGO_SECRET_KEY="$DJ" --from-literal=PAYMENT_DATABASE_URL="$PAY_DB" --from-literal=LEDGER_DATABASE_URL="$LED_DB" --from-literal=REDIS_URL="$REDIS" --dry-run=client -o yaml | kubectl apply -f -
   ```
5. **Apply the four service files:**
   ```powershell
   kubectl apply -f deploy/k8s/payment.yaml -f deploy/k8s/ledger.yaml -f deploy/k8s/gateway.yaml -f deploy/k8s/ai.yaml
   ```
6. **Watch, then reach the gateway** (same as the Helm path):
   ```powershell
   kubectl -n ledgerstream get pods
   kubectl -n ledgerstream port-forward svc/ledgerstream-gateway 8010:8010
   kubectl -n ledgerstream port-forward svc/ledgerstream-ai 8030:8030
   ```
7. **Remove everything:**
   ```powershell
   kubectl delete -f deploy/k8s/ai.yaml -f deploy/k8s/gateway.yaml -f deploy/k8s/ledger.yaml -f deploy/k8s/payment.yaml -f deploy/k8s/00-config.yaml
   ```

> **Order matters** — step 4 (real Secret) must run before step 5 (the pods), because a pod
> reads its env at startup. If you applied the pods first, fix the Secret then
> `kubectl -n ledgerstream rollout restart deployment -l app=ledgerstream`.
>
> **Helm vs raw:** Helm renders the templates, applies them, and tracks a *release* (one
> `helm uninstall` cleans up all 13 objects). With raw files you apply the finished YAML
> yourself and clean up with `kubectl delete`. Same objects, no release bookkeeping.

#### Optional: expose the API with an Ingress (instead of port-forward)

Steps 1–7 reach the API with `kubectl port-forward`, which needs nothing extra. If you'd
rather hit a real URL like `http://ledgerstream.local/`, apply the optional
[`ingress.yaml`](../deploy/k8s/ingress.yaml) — but it takes **three steps, and each is
required for a reason.** An Ingress is only *routing rules*; on its own it does nothing.

1. **Install an ingress controller.** The Ingress object is inert until a controller is
   running to read its rules and actually route traffic. Docker Desktop's Kubernetes ships
   **without** one, so install NGINX:
   ```powershell
   kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml
   ```
   *Why:* no controller = nobody enforces the rules = no routing. This is the component that
   watches Ingress objects and forwards requests to the right Service.

2. **Point the hostname at your machine.** Add this line to your hosts file
   (`C:\Windows\System32\drivers\etc\hosts`, edit as Administrator):
   ```
   127.0.0.1  ledgerstream.local
   ```
   *Why:* the Ingress routes by **host** (`ledgerstream.local`). Your computer has no idea
   what that name is until you map it to an IP — this line points it at localhost, where the
   controller listens.

3. **Apply the Ingress rules:**
   ```powershell
   kubectl apply -f deploy/k8s/ingress.yaml
   ```
   *Why:* this is the routing table itself — `/` → the gateway Service (`:8010`), `/ai` → the
   AI Service (`:8030`). The controller from step 1 reads it and starts forwarding.

Then browse **http://ledgerstream.local/** (gateway) or **http://ledgerstream.local/ai** (AI).
Check it took effect — the `ADDRESS` column fills in once the controller claims it:
```powershell
kubectl -n ledgerstream get ingress
```

> **When you'd skip all this:** for local testing, `port-forward` (step 6) is simpler and needs
> none of the above. Ingress matters when you want a stable URL, host/path routing, or TLS —
> and in a **cloud cluster the controller is usually already there**, so only step 3 (apply the
> rules) is yours to do. The Helm equivalent of this whole section is
> `--set ingress.enabled=true` (steps 1 and 2 are still required either way).

### 6.4 — Run Kafka in-cluster (optional, for the full event pipeline)

By default the worker pods point at `kafka:9092`, which doesn't exist in the cluster (Kafka is
external), so they crash-loop — fine if you only want the web APIs. To run the **whole event
pipeline in k8s**, add a single-node Kafka + Schema Registry to the namespace. Their Services
are named `kafka` / `schema-registry`, so the pods resolve them with **no other change**.

**Helm:** flip the toggle (adds 4 objects: Kafka + Schema Registry, each a Deployment + Service):
```powershell
helm upgrade --install ledgerstream deploy/helm/ledgerstream --namespace ledgerstream --set kafka.enabled=true ...(your other flags)...
```

**Raw manifests:** apply the optional file:
```powershell
kubectl apply -f deploy/k8s/kafka.yaml
```

Then the worker pods connect on their own once Kafka is up (~30–60s; they retry). If you'd
scaled the workers to 0, scale them back:
```powershell
kubectl -n ledgerstream scale deployment ledgerstream-ledger-consume-payments ledgerstream-payment-outbox-relay ledgerstream-payment-ledger-outcomes --replicas=1
```

**Watch it with Kafka UI (also in-cluster).** A Kafka UI on your host can't reach the cluster's
`kafka` Service, so run it inside too.

- **Helm:** it's rendered **automatically** with `kafka.enabled=true` (no extra command). To skip
  it, add `--set kafka.uiEnabled=false`.
- **Raw manifests:** apply it explicitly:
  ```powershell
  kubectl apply -f deploy/k8s/kafka-ui.yaml
  ```

Then port-forward and open it (both methods):
```powershell
kubectl -n ledgerstream port-forward svc/kafka-ui 8085:8080
```
Browse **http://localhost:8085** to see `payments.events` / `ledger.events`, the Avro messages,
and consumer-group lag.

> ⚠️ **Demo-grade (Tier 3):** single broker, KRaft, **ephemeral `emptyDir` storage** (data lost
> on pod restart), topics auto-created with 1 partition. Production uses a StatefulSet + PVCs +
> multiple brokers (or the Strimzi operator). This exists only to make the in-cluster pipeline
> demoable — the honest default is "Kafka is an external managed service."

### Troubleshooting (real issues hit deploying this)

| Symptom | Cause & fix |
|---|---|
| `winget : not recognized` | The Microsoft Store "App Installer" isn't present. Use the direct Helm download in step 1 (no winget needed). |
| `helm : not recognized` **after** installing | PATH only refreshes in a NEW terminal. Either open a fresh PowerShell, or add it to the current session: `$env:Path += ";$env:USERPROFILE\tools\helm"`. Verify the file exists first: `& "$env:USERPROFILE\tools\helm\helm.exe" version`. |
| `kind : not recognized` | You don't need kind if you use Docker Desktop's Kubernetes. (Or install it: direct binary, or a package manager.) |
| Bash command fails in PowerShell (`\` at line-ends, `$VAR` empty) | PowerShell uses backtick `` ` `` for line-continuation, not `\`, and vars are set `$X = "..."`. Simplest: run the `helm` command as ONE line (as in step 6). |
| Pods stuck `ImagePullBackOff` | k8s can't find the image. On Docker Desktop, make sure you built with the `:dev` tag and installed with `image.registry=docker.io/library` + `image.tag=dev`; the chart's `imagePullPolicy: IfNotPresent` then uses the local image. |
| The **Kafka worker** pods (`*-outbox-relay`, `*-consume-payments`, `*-ledger-outcomes`) `CrashLoopBackOff`, log `Failed to resolve 'kafka:9092': No address associated with hostname` | The pods point at `kafka:9092`, but nothing by that name exists in the cluster (Kafka is in your host docker-compose). The **web** pods work fine without it. Fixes (see 6.4): **(simplest)** run Kafka in-cluster — Helm `--set kafka.enabled=true`, or raw `kubectl apply -f deploy/k8s/kafka.yaml`, both create the `kafka`/`schema-registry` Services the pods already resolve; **(or)** point at the host broker with `--set externalEnv.KAFKA_BOOTSTRAP_SERVERS=host.docker.internal:29092 ...` (needs the compose broker to advertise that address, so it's fiddlier). Just want the noise gone? Scale only the workers to 0 by name: `kubectl -n ledgerstream scale deployment ledgerstream-ledger-consume-payments ledgerstream-payment-outbox-relay ledgerstream-payment-ledger-outcomes --replicas=0`. |
| Web pods `Running` but API returns DB errors | No migration Job runs in-cluster. Your Neon DBs are already migrated from native dev, so reads work; a fresh DB would need `manage.py migrate` first (a k8s `Job` is the production way — left out as showcase scope). |
| `FileNotFoundError: Could not locate schemas/avro` in the **ledger consumer** | The ledger consumer produces `LedgerOutcome` and needs the Avro schemas baked into its image. The `services/ledger/Dockerfile` `COPY schemas /app/schemas` provides them — rebuild the ledger image and restart: `docker build -f services/ledger/Dockerfile -t "ledgerstream-ledger:dev" .` then `kubectl -n ledgerstream rollout restart deployment/ledgerstream-ledger-consume-payments`. |
| In-cluster `schema-registry` `CrashLoopBackOff` — exit code 1 in ~3s, log ends at `Configuring ... PORT is deprecated` | Two single-node/k8s gotchas, **both already fixed** in `deploy/k8s/kafka.yaml` + the Helm template (so a fresh apply won't hit them): (1) Kubernetes injects `SCHEMA_REGISTRY_PORT` "service-link" env vars that the Confluent image mistakes for its port config → set `enableServiceLinks: false` on the pod; (2) the internal `_schemas` topic defaults to replication factor 3, rejected by a single broker → set `SCHEMA_REGISTRY_KAFKASTORE_TOPIC_REPLICATION_FACTOR: "1"`. Rolled your own Kafka manifest? Add both. |
| Rebuilt an image but the pod still runs the OLD code (e.g. a Dockerfile fix never takes) | **On Docker Desktop's Kubernetes, reusing a tag does NOT work.** `imagePullPolicy: IfNotPresent` + the same `:dev` tag makes the kubelet keep its **cached** image — and `rollout restart`, deleting the pod, deleting the namespace, and even `docker rmi` all fail to clear that node-level cache. The reliable fix is a **brand-new tag** the kubelet has never seen: `docker build ... -t ledgerstream-<svc>:v2 .`, verify with `docker run --rm ledgerstream-<svc>:v2 ls /app/...`, then `kubectl -n ledgerstream set image deployment/<name> <container>=ledgerstream-<svc>:v2` (or Helm `--set image.tag=v2`). |
| Can't reach `localhost:8010` | The `port-forward` (step 8) must stay running in its own terminal; open a second terminal for `curl`/smoke tests. |

---

## Part 7 — ⚠️ Scaffolded — be ready to explain

- **One image, many Deployments.** Default `CMD` = web; a Deployment's `command:` override =
  a worker. Workers get no Service (nothing dials them; they pull from Kafka).
- **In-cluster DNS.** `http://ledgerstream-payment:8000` resolves via k8s DNS; Helm computes
  it from the release name so gateway/ai find upstreams without hardcoding.
- **Probes.** Readiness gates traffic (a failing pod leaves the Service's endpoints);
  liveness restarts a wedged pod. We use TCP probes; `httpGet /health/ready` is the upgrade.
- **Secrets flow.** Placeholder → `--set-string`/`set_sensitive` at install → k8s `Secret` →
  `envFrom` into the pod. Never in git, never in Terraform state.
- **CI service containers vs hermetic tests.** Real Postgres for the DB suites; fakes/mocks
  for the stateless ones — and the build job is gated behind all of them.
- **Why data stores are external.** The chart deploys the stateless tier only; managed
  data services are inputs (URLs). Stateful sets for real databases are a deliberate non-goal.

---

## Part 8 — Mini-glossary (new terms this phase)

| Term | Meaning |
|---|---|
| **Image / container** | A built, immutable filesystem + default command (image); a running instance of it (container). |
| **Registry (GHCR)** | Where images are stored/pulled. GitHub Container Registry = `ghcr.io`. |
| **Pod** | The smallest k8s unit — one or more containers scheduled together. |
| **Deployment** | Declares *N replicas* of a pod template and keeps them running (rollouts, self-heal). |
| **Service (k8s)** | A stable in-cluster name + virtual IP load-balancing to a set of pods. |
| **ConfigMap / Secret** | Key-value env delivered to pods (Secret = base64, for sensitive values). |
| **Probe** | A health check k8s runs: **readiness** (gate traffic) / **liveness** (restart if wedged). |
| **Helm / chart / release** | k8s's package manager; a chart is the templated package; a release is one install of it. |
| **values.yaml** | The chart's inputs; templates render k8s YAML from them. |
| **Terraform / provider / state** | Declarative infra tool; a provider talks to one API (k8s, helm); state is its record of reality. |
| **CI/CD** | Continuous Integration (test/build every commit) / Continuous Delivery (ship the artifact). |
| **Service container** | A throwaway container (e.g. Postgres) GitHub Actions runs alongside a job for tests. |
