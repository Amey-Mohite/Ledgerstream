# main.tf — the actual resources. Terraform builds a dependency graph from the
# references between blocks and creates/updates them in the right order.

# --- Provider configuration ---------------------------------------------------
# Both providers point at the SAME cluster via your kubeconfig, so `apply` works
# against kind, minikube, or a cloud cluster with no code change.
provider "kubernetes" {
  config_path = var.kubeconfig
  # If kube_context is "", pass null so Terraform uses the file's current-context.
  config_context = var.kube_context != "" ? var.kube_context : null
}

provider "helm" {
  # The helm provider runs Helm under the hood; it needs its own cluster access.
  kubernetes {
    config_path    = var.kubeconfig
    config_context = var.kube_context != "" ? var.kube_context : null
  }
}

# --- 1. The namespace Terraform owns for this release -------------------------
resource "kubernetes_namespace" "ledgerstream" {
  metadata {
    name = var.namespace
  }
}

# --- 2. The Helm release (installs the chart from ../helm/ledgerstream) --------
resource "helm_release" "ledgerstream" {
  name = var.release_name
  # Referencing the namespace resource makes Terraform create it FIRST (implicit
  # dependency — no manual ordering needed).
  namespace = kubernetes_namespace.ledgerstream.metadata[0].name
  # Local path to the chart from Part 3 (path.module = this directory).
  chart = "${path.module}/../helm/ledgerstream"

  # Block `apply` until every Deployment is Ready (or time out) — so a green
  # apply means the app actually came up.
  wait    = true
  timeout = 300

  # Plain (non-secret) values → equivalent to `helm --set image.registry=...`.
  set {
    name  = "image.registry"
    value = var.image_registry
  }
  set {
    name  = "image.tag"
    value = var.image_tag
  }

  # Fan the secrets map into one `--set-string secretEnv.<KEY>=<value>` each.
  # set_sensitive (not set) keeps the values OUT of plan output and logs.
  dynamic "set_sensitive" {
    for_each = var.secrets
    content {
      name  = "secretEnv.${set_sensitive.key}"
      value = set_sensitive.value
    }
  }
}
