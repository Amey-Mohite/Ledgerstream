# outputs.tf — values Terraform prints after `apply` (and exposes to other
# configs/modules). Handy for scripting the next step.

output "namespace" {
  description = "Namespace the release was installed into."
  # Read back from the created resource (proves it exists).
  value = kubernetes_namespace.ledgerstream.metadata[0].name
}

output "release_name" {
  description = "Installed Helm release name."
  value       = helm_release.ledgerstream.name
}

output "release_status" {
  description = "Helm release status after apply (e.g. 'deployed')."
  value       = helm_release.ledgerstream.status
}
