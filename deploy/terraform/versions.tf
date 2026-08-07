# versions.tf — pins Terraform + provider versions so everyone (and CI) uses
# compatible tooling. Terraform reads this first.

terraform {
  # Minimum Terraform CLI version this config is written for.
  required_version = ">= 1.5"

  # Providers are plugins that talk to one API each. Terraform downloads these on
  # `terraform init`. We need two: one to create the namespace, one to install
  # the Helm chart — so `terraform apply` stands the whole app up in one command.
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes" # where to fetch the plugin from
      version = "~> 2.30"              # "~>" = allow 2.x (>=2.30, <3.0)
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.14"
    }
  }
}
