# s1d1-tantive as one Cloud Run service. Cloud Run cannot pull from ghcr.io: copy the published image
# (ghcr.io/scombat/s1d1-tantive:0.8.1-small-onnx-int8, or another variant) into your Artifact Registry and give that
# path as `image` (see README). Adapt: project_id, region, image, and cpu/memory for another variant.
terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  description = "Google Cloud project that holds the service."
  type        = string
}

variable "region" {
  description = "Cloud Run region."
  type        = string
  default     = "europe-west1"
}

variable "image" {
  description = "Image in your Artifact Registry, a copy of the published one (small needs 1 GiB, full 2 GiB)."
  type        = string
}

variable "s1d1_tiers" {
  description = "S1D1_TIERS, e.g. TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0. Must match the tier names your gateway sends, otherwise the service answers 422."
  type        = string
}

variable "s1d1_api_key" {
  description = "S1D1_API_KEY, stored in Secret Manager and injected as a secret. Empty = no key (then keep the service private)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "cpu" {
  description = "CPU limit; 1 with 2Gi suits small, 2 with 4Gi suits full."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory limit."
  type        = string
  default     = "2Gi"
}

variable "min_instances" {
  description = "Minimum instances. 1 keeps the model loaded and avoids the cold start (billed while idle); 0 scales to zero."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum instances."
  type        = number
  default     = 3
}

variable "allow_unauthenticated" {
  description = "true = anyone may call the URL (then set s1d1_api_key); false = callers need the run.invoker role."
  type        = bool
  default     = false
}

locals {
  has_key = var.s1d1_api_key != ""
}

resource "google_service_account" "tantive" {
  account_id   = "s1d1-tantive"
  display_name = "s1d1-tantive Cloud Run service"
}

resource "google_secret_manager_secret" "api_key" {
  count     = local.has_key ? 1 : 0
  secret_id = "s1d1-api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "api_key" {
  count       = local.has_key ? 1 : 0
  secret      = google_secret_manager_secret.api_key[0].id
  secret_data = var.s1d1_api_key
}

# the service account may read this one secret
resource "google_secret_manager_secret_iam_member" "reader" {
  count     = local.has_key ? 1 : 0
  secret_id = google_secret_manager_secret.api_key[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.tantive.email}"
}

resource "google_cloud_run_v2_service" "tantive" {
  name     = "s1d1-tantive"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.tantive.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      ports {
        container_port = 8788
      }
      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
      }

      env {
        name  = "S1D1_TIERS"
        value = var.s1d1_tiers
      }
      env {
        name  = "S1D1_FORMATS"
        value = "systemone,decisions,classify"
      }
      env {
        name  = "S1D1_DEADLINE_MS"
        value = "800"
      }
      dynamic "env" {
        for_each = local.has_key ? [1] : []
        content {
          name = "S1D1_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.api_key[0].secret_id
              version = "latest"
            }
          }
        }
      }

      # the model takes a few seconds to load: 6 x 10 s before the instance is given up
      startup_probe {
        http_get {
          path = "/ready"
        }
        period_seconds    = 10
        failure_threshold = 6
      }
      liveness_probe {
        http_get {
          path = "/health"
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_version.api_key, google_secret_manager_secret_iam_member.reader]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_unauthenticated ? 1 : 0
  name     = google_cloud_run_v2_service.tantive.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  description = "URL of the service."
  value       = google_cloud_run_v2_service.tantive.uri
}
