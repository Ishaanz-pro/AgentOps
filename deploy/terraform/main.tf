# Infrastructure for the Cloud Run deployment, as code.
# NOT applied in the authoring environment (Terraform was not installed). `terraform validate` before use.
#
#   terraform init
#   terraform apply -var project=my-proj -var image=asia-south1-docker.pkg.dev/my-proj/agentops/agentops:v1
#
# Build and push the image first (Cloud Build):
#   gcloud builds submit --tag asia-south1-docker.pkg.dev/my-proj/agentops/agentops:v1 .

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = { source = "hashicorp/google", version = ">= 6.0" }
  }
}

variable "project" { type = string }
variable "region" {
  type    = string
  default = "asia-south1"
}
variable "image" { type = string }
variable "model" {
  type    = string
  default = "gemini-3.5-flash"
}
variable "public" {
  type        = bool
  default     = false
  description = "Allow unauthenticated access (demo link). Pair with daily_chat_budget."
}
variable "daily_chat_budget" {
  type    = number
  default = 200
}

provider "google" {
  project = var.project
  region  = var.region
}

locals {
  apis = ["run.googleapis.com", "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
  "aiplatform.googleapis.com", "logging.googleapis.com", "cloudtrace.googleapis.com", "calendar-json.googleapis.com"]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "repo" {
  repository_id = "agentops"
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# Least-privilege runtime identity: call Gemini on Vertex AI, write logs and traces. No keys exist.
resource "google_service_account" "run" {
  account_id   = "agentops-run"
  display_name = "AgentOps Cloud Run runtime"
}

resource "google_project_iam_member" "run_roles" {
  for_each = toset(["roles/aiplatform.user", "roles/logging.logWriter", "roles/cloudtrace.agent"])
  project  = var.project
  role     = each.value
  member   = "serviceAccount:${google_service_account.run.email}"
}

resource "google_cloud_run_v2_service" "agentops" {
  name     = "agentops"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run.email
    timeout         = "120s"
    scaling {
      min_instance_count = 0
      max_instance_count = 1 # in-process sessions/traces/confirmations; see README limitations
    }
    containers {
      image = var.image
      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = "global"
      }
      env {
        name  = "AGENTOPS_MODE"
        value = "gemini"
      }
      env {
        name  = "AGENTOPS_EMBEDDINGS"
        value = "gemini"
      }
      env {
        name  = "AGENTOPS_MODEL"
        value = var.model
      }
      env {
        name  = "AGENTOPS_DAILY_CHAT_BUDGET"
        value = tostring(var.daily_chat_budget)
      }
      startup_probe {
        http_get { path = "/health" }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 12
      }
    }
  }
  depends_on = [google_project_service.apis, google_project_iam_member.run_roles]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.public ? 1 : 0
  name     = google_cloud_run_v2_service.agentops.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" { value = google_cloud_run_v2_service.agentops.uri }
output "service_account" { value = google_service_account.run.email }
