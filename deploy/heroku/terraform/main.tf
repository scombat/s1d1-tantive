# s1d1-tantive on Heroku. Heroku cannot pull an image from a registry: it builds the repository's heroku.yml, which
# builds deploy/heroku/Dockerfile (FROM ghcr.io/scombat/s1d1-tantive:0.8.0-small-onnx-int8). The service listens on
# Heroku's $PORT there (8788 elsewhere). Adapt: app_name, source_url (a tarball of your fork or of a release).
terraform {
  required_version = ">= 1.5"
  required_providers {
    heroku = {
      source  = "heroku/heroku"
      version = "~> 5.4"
    }
  }
}

variable "app_name" {
  description = "Heroku app name, unique across Heroku."
  type        = string
}

variable "region" {
  description = "Heroku region, us or eu."
  type        = string
  default     = "eu"
}

variable "source_url" {
  description = "Tarball of the repository to build; it must be reachable by Heroku (public repository)."
  type        = string
  default     = "https://github.com/scombat/s1d1-tantive/tarball/main"
}

variable "source_version" {
  description = "Label of that source (a tag or commit), shown on the release; changing it triggers a new build."
  type        = string
  default     = "0.8.0"
}

variable "dyno_size" {
  description = "Dyno size. The small variant needs 1 GiB: standard-2x is the smallest."
  type        = string
  default     = "standard-2x"
}

variable "s1d1_tiers" {
  description = "S1D1_TIERS, e.g. TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0. Must match the tier names your gateway sends, otherwise the service answers 422."
  type        = string
}

variable "s1d1_api_key" {
  description = "S1D1_API_KEY, a config var (Heroku web apps are public: set one). Empty = no key."
  type        = string
  default     = ""
  sensitive   = true
}

resource "heroku_app" "tantive" {
  name   = var.app_name
  region = var.region
  stack  = "container"

  config_vars = {
    S1D1_TIERS       = var.s1d1_tiers
    S1D1_FORMATS     = "systemone,decisions,classify"
    S1D1_DEADLINE_MS = "800"
  }
  sensitive_config_vars = var.s1d1_api_key == "" ? {} : { S1D1_API_KEY = var.s1d1_api_key }
}

resource "heroku_build" "tantive" {
  app_id = heroku_app.tantive.id

  source {
    url     = var.source_url
    version = var.source_version
  }
}

resource "heroku_formation" "web" {
  app_id     = heroku_app.tantive.id
  type       = "web"
  quantity   = 1
  size       = var.dyno_size
  depends_on = [heroku_build.tantive]
}

# Heroku has no probe settings: the router only checks that the process binds $PORT; /ready tells when the model is loaded
output "url" {
  description = "URL of the app."
  value       = heroku_app.tantive.web_url
}
