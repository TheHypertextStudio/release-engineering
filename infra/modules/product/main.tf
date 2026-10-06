terraform {
  required_version = ">= 1.9.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.5.0"
    }
  }
}

locals {
  phases = setunion(toset(["candidate", "promote"]), length(var.pin_update_credential_names) > 0 ? toset(["pins"]) : toset([]))
  workflow_conditions = {
    candidate = "(((assertion.event_name == 'push' || assertion.event_name == 'repository_dispatch') && assertion.job_workflow_ref == '${var.tooling_repository}/.github/workflows/candidate.yml@${var.tooling_revision}') || (assertion.event_name == 'workflow_dispatch' && assertion.job_workflow_ref == '${var.tooling_repository}/.github/workflows/diagnose.yml@${var.tooling_revision}'))"
    promote   = "((assertion.event_name == 'workflow_dispatch' && assertion.job_workflow_ref == '${var.tooling_repository}/.github/workflows/promote.yml@${var.tooling_revision}') || ((assertion.event_name == 'schedule' || assertion.event_name == 'workflow_dispatch') && assertion.job_workflow_ref == '${var.tooling_repository}/.github/workflows/reconcile-store.yml@${var.tooling_revision}'))"
    pins      = "(assertion.event_name == 'schedule' || assertion.event_name == 'workflow_dispatch') && assertion.job_workflow_ref == '${var.tooling_repository}/.github/workflows/update-pins.yml@${var.tooling_revision}'"
  }
}

data "google_storage_bucket" "distribution" {
  name = var.distribution_bucket
}

resource "google_service_account" "lifecycle" {
  for_each     = local.phases
  project      = var.project_id
  account_id   = "${var.product}-${each.key}"
  display_name = "${var.product} ${each.key} lifecycle"
}

resource "google_iam_workload_identity_pool" "product" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.product}-github"
  display_name              = "${var.product} GitHub lifecycle"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  for_each                           = local.phases
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.product.workload_identity_pool_id
  workload_identity_pool_provider_id = each.key
  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.phase"         = "'${each.key}'"
  }
  attribute_condition = "assertion.repository_id == '${var.repository_id}' && assertion.repository_owner_id == '${var.owner_id}' && assertion.ref == 'refs/heads/${var.default_branch}' && ${local.workflow_conditions[each.key]}"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "federated" {
  for_each           = local.phases
  service_account_id = google_service_account.lifecycle[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.product.name}/attribute.phase/${each.key}"
}

resource "google_secret_manager_secret" "bindings" {
  for_each  = var.credential_names
  project   = var.project_id
  secret_id = "${var.product}-${each.key}"
  replication {
    auto {}
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_secret_manager_secret_iam_member" "candidate" {
  for_each  = setsubtract(var.credential_names, var.pin_update_credential_names)
  project   = var.project_id
  secret_id = google_secret_manager_secret.bindings[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.lifecycle["candidate"].email}"
}

resource "google_secret_manager_secret_iam_member" "pins" {
  for_each  = var.pin_update_credential_names
  project   = var.project_id
  secret_id = google_secret_manager_secret.bindings[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.lifecycle["pins"].email}"
}

resource "google_secret_manager_secret_iam_member" "promotion" {
  for_each  = var.promotion_credential_names
  project   = var.project_id
  secret_id = google_secret_manager_secret.bindings[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.lifecycle["promote"].email}"
}

resource "google_storage_bucket_iam_member" "publisher" {
  bucket = data.google_storage_bucket.distribution.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.lifecycle["promote"].email}"
  condition {
    title      = "Product namespace"
    expression = "resource.name.startsWith('projects/_/buckets/${var.distribution_bucket}/objects/${var.product}/')"
  }
}

resource "google_service_account_iam_member" "runtime" {
  for_each           = var.runtime_service_accounts
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.lifecycle["promote"].email}"
}
