# variables.tf — the INPUTS to this config. Set them via -var, a *.tfvars file,
# or TF_VAR_* env vars. Each block declares one input with a type + default;
# no default = required.

# --- How Terraform reaches your cluster --------------------------------------
variable "kubeconfig" {
  description = "Path to the kubeconfig file (works for kind, minikube, EKS/GKE/AKS)."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "kubeconfig context to target (empty = the file's current-context)."
  type        = string
  default     = ""
}

# --- Where to install ---------------------------------------------------------
variable "namespace" {
  description = "Namespace to create and install into."
  type        = string
  default     = "ledgerstream"
}

variable "release_name" {
  description = "Helm release name (also the prefix on every object's name)."
  type        = string
  default     = "ledgerstream"
}

# --- Image coordinates passed through to the chart ----------------------------
variable "image_registry" {
  description = "Registry + owner, e.g. ghcr.io/your-github-username."
  type        = string
  # No default → you MUST supply it (there's no sensible universal value).
}

variable "image_tag" {
  description = "Image tag to deploy (e.g. latest, or a git SHA)."
  type        = string
  default     = "latest"
}

# --- The sensitive env the chart needs ----------------------------------------
variable "secrets" {
  description = "Map of secret env name -> value, injected into the chart's Secret."
  type        = map(string)
  # sensitive = true → Terraform redacts these in plan/apply output and logs.
  sensitive = true
  # Empty default so `validate`/`plan` work with no secrets; supply real values
  # via `TF_VAR_secrets='{...}'` or a gitignored *.tfvars — NEVER commit them.
  default = {}
}
