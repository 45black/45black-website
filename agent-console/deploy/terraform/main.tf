# One-shot Terraform for the 45black agent console stack.
#
#   - Artifact Registry repo for the Docker image
#   - Firestore database (Native mode, europe-west1)
#   - Service accounts: agent-console (runtime), agent-scheduler (scheduler)
#   - IAM bindings for Vertex AI, Firestore, Secret Manager, Cloud Run invoke
#   - Secret Manager entries (empty — populate with `gcloud secrets versions add`)
#
# Usage:
#   cd deploy/terraform
#   terraform init
#   terraform apply -var="project_id=45black-agents"
#
# Re-run safely: all resources are idempotent.

terraform {
  required_version = ">= 1.7"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region (Vertex AI + Cloud Run + Firestore)"
  type        = string
  default     = "europe-west1"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# --- APIs --------------------------------------------------------------

locals {
  apis = [
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)
  service  = each.value

  disable_on_destroy = false
}

# --- Artifact Registry --------------------------------------------------

resource "google_artifact_registry_repository" "agents" {
  location      = var.region
  repository_id = "agents"
  format        = "DOCKER"
  description   = "45black agent console images"

  depends_on = [google_project_service.apis]
}

# --- Firestore ----------------------------------------------------------

resource "google_firestore_database" "default" {
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  depends_on = [google_project_service.apis]
}

# --- Service accounts ---------------------------------------------------

resource "google_service_account" "console" {
  account_id   = "agent-console"
  display_name = "45black Agent Console runtime"
}

resource "google_service_account" "scheduler" {
  account_id   = "agent-scheduler"
  display_name = "45black Agent Console scheduler"
}

# Runtime SA needs: Vertex user, Firestore user, Secret accessor, log writer.
resource "google_project_iam_member" "console_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.console.email}"
}

resource "google_project_iam_member" "console_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.console.email}"
}

resource "google_project_iam_member" "console_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.console.email}"
}

resource "google_project_iam_member" "console_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.console.email}"
}

# Scheduler SA needs to invoke the Cloud Run service.
# (Role binding is on the service itself — set after `gcloud run deploy`.)

# --- Secrets (empty; populate out-of-band) -----------------------------

resource "google_secret_manager_secret" "mcp_token" {
  secret_id = "mcp-gateway-token"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "gmail_oauth" {
  secret_id = "gmail-oauth-refresh-token"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "oauth_client_id" {
  secret_id = "google-oauth-client-id"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "oauth_client_secret" {
  secret_id = "google-oauth-client-secret"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# --- Outputs ------------------------------------------------------------

output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "console_sa" {
  value = google_service_account.console.email
}

output "scheduler_sa" {
  value = google_service_account.scheduler.email
}

output "artifact_repo" {
  value = google_artifact_registry_repository.agents.id
}
