# s1d1-tantive as one Railway service that runs the published image, in a project and environment you already have.
# The provider is the community one (Railway publishes none); token: RAILWAY_TOKEN in the environment.
# Adapt: project_id, environment_id, and `image` (small needs 1 GiB: a Trial or Hobby plan).
terraform {
  required_version = ">= 1.5"
  required_providers {
    railway = {
      source  = "terraform-community-providers/railway"
      version = "~> 0.6"
    }
  }
}

variable "project_id" {
  description = "Existing Railway project."
  type        = string
}

variable "environment_id" {
  description = "Existing environment of that project (production)."
  type        = string
}

variable "image" {
  description = "Image to run, one per model variant (full needs 2 GiB, small 1 GiB, tiny 512 MiB)."
  type        = string
  default     = "ghcr.io/scombat/s1d1-tantive:0.6.0-small-onnx-int8"
}

variable "s1d1_tiers" {
  description = "S1D1_TIERS, e.g. TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0. Must match the tier names your gateway sends, otherwise the service answers 422."
  type        = string
}

variable "s1d1_api_key" {
  description = "S1D1_API_KEY, a service variable (the public domain is open: set one). Empty = no key."
  type        = string
  default     = ""
  sensitive   = true
}

resource "railway_service" "tantive" {
  name         = "s1d1-tantive"
  project_id   = var.project_id
  source_image = var.image

  # ready only once the model is loaded; Railway waits for it before it switches traffic
  healthcheck_path    = "/ready"
  healthcheck_timeout = 120
}

resource "railway_variable_collection" "env" {
  service_id     = railway_service.tantive.id
  environment_id = var.environment_id

  variables = concat(
    [
      { name = "PORT", value = "8788" }, # Railway routes the public domain to PORT
      { name = "S1D1_TIERS", value = var.s1d1_tiers },
      { name = "S1D1_FORMATS", value = "systemone,decisions,classify" },
      { name = "S1D1_DEADLINE_MS", value = "800" },
    ],
    var.s1d1_api_key == "" ? [] : [{ name = "S1D1_API_KEY", value = var.s1d1_api_key }],
  )
}

resource "railway_service_domain" "public" {
  service_id     = railway_service.tantive.id
  environment_id = var.environment_id
  subdomain      = "s1d1-tantive"
  target_port    = 8788
}

output "url" {
  description = "Public URL of the service."
  value       = "https://${railway_service_domain.public.domain}"
}
