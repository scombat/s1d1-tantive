# s1d1-tantive as one Azure Container App. Adapt: the three "existing" variables (your resource group and
# Container Apps environment) and, for another model variant, `image` with `memory`/`cpu`.
terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

variable "subscription_id" {
  description = "Azure subscription that holds the environment."
  type        = string
}

variable "resource_group_name" {
  description = "Existing resource group of the Container Apps environment."
  type        = string
}

variable "environment_id" {
  description = "Resource ID of the existing Container Apps environment."
  type        = string
}

variable "s1d1_tiers" {
  description = "S1D1_TIERS, e.g. TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0. Must match the tier names your gateway sends, otherwise the service answers 422."
  type        = string
}

variable "s1d1_api_key" {
  description = "S1D1_API_KEY, stored as a Container App secret. Empty = no key (set one when ingress is external)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "image" {
  description = "Image to run, one per model variant (full needs 2 GiB, small 1 GiB, tiny 512 MiB)."
  type        = string
  default     = "ghcr.io/scombat/s1d1-tantive:0.6.0-full-onnx-int8"
}

variable "cpu" {
  description = "vCPU of the container; 2.0 with 4Gi suits full, 1.0 with 2Gi suits small."
  type        = number
  default     = 2.0
}

variable "memory" {
  description = "Memory of the container, in the Container Apps format."
  type        = string
  default     = "4Gi"
}

variable "external_ingress" {
  description = "true = reachable from the internet (then set s1d1_api_key); false = inside the environment's VNet."
  type        = bool
  default     = false
}

variable "min_replicas" {
  description = "Minimum replicas. 1 keeps the model loaded; 0 scales to zero and the first call waits for it."
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Maximum replicas."
  type        = number
  default     = 3
}

resource "azurerm_container_app" "tantive" {
  name                         = "s1d1-tantive"
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.environment_id
  revision_mode                = "Single"

  # the key is a secret, injected below as a secret environment variable; no secret when it is empty
  dynamic "secret" {
    for_each = var.s1d1_api_key == "" ? [] : [1]
    content {
      name  = "s1d1-api-key"
      value = var.s1d1_api_key
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "tantive"
      image  = var.image
      cpu    = var.cpu
      memory = var.memory

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
        for_each = var.s1d1_api_key == "" ? [] : [1]
        content {
          name        = "S1D1_API_KEY"
          secret_name = "s1d1-api-key"
        }
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8788
        path      = "/health"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 8788
        path      = "/ready"
      }
      # the model takes a few seconds to load: 6 x 10 s before the first failure counts
      startup_probe {
        transport               = "HTTP"
        port                    = 8788
        path                    = "/ready"
        interval_seconds        = 10
        failure_count_threshold = 6
      }
    }

    # scale out by concurrent requests per replica
    http_scale_rule {
      name                = "http-concurrency"
      concurrent_requests = "8"
    }
  }

  ingress {
    external_enabled = var.external_ingress
    target_port      = 8788
    transport        = "http"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

output "url" {
  description = "URL of the service."
  value       = "https://${azurerm_container_app.tantive.ingress[0].fqdn}"
}
