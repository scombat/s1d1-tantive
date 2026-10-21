# s1d1-tantive as one Render web service that runs the published image (tiny fits the free plan's 512 MB).
# Adapt: `plan` and `image_url` together (small: starter or more; full: standard or more). Credentials of the
# provider: RENDER_API_KEY and RENDER_OWNER_ID in the environment.
terraform {
  required_version = ">= 1.5"
  required_providers {
    render = {
      source  = "render-oss/render"
      version = "~> 1.9"
    }
  }
}

variable "image_url" {
  description = "Image to run, one per model variant (full needs 2 GiB, small 1 GiB, tiny 512 MiB)."
  type        = string
  default     = "ghcr.io/scombat/s1d1-tantive:0.6.0-tiny-onnx-int8"
}

variable "plan" {
  description = "Render plan; free has 512 MB (tiny only)."
  type        = string
  default     = "free"
}

variable "region" {
  description = "Render region."
  type        = string
  default     = "frankfurt"
}

variable "s1d1_tiers" {
  description = "S1D1_TIERS, e.g. TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0. Must match the tier names your gateway sends, otherwise the service answers 422."
  type        = string
}

variable "s1d1_api_key" {
  description = "S1D1_API_KEY. Empty = Render generates a random key, shown in the dashboard (a web service is always public)."
  type        = string
  default     = ""
  sensitive   = true
}

resource "render_web_service" "tantive" {
  name   = "s1d1-tantive"
  plan   = var.plan
  region = var.region

  runtime_source = {
    image = {
      image_url = var.image_url
    }
  }

  # ready only once the model is loaded
  health_check_path = "/ready"

  env_vars = {
    # Render routes traffic to PORT; the service listens on 8788
    PORT             = { value = "8788" }
    S1D1_TIERS       = { value = var.s1d1_tiers }
    S1D1_FORMATS     = { value = "systemone,decisions,classify" }
    S1D1_DEADLINE_MS = { value = "800" }
    S1D1_API_KEY     = var.s1d1_api_key == "" ? { generate_value = true } : { value = var.s1d1_api_key }
  }
}

output "url" {
  description = "URL of the service."
  value       = render_web_service.tantive.url
}
