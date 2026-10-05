terraform {
  required_version = ">= 1.9.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.5.0"
    }
  }
}
variable "project_id" { type = string }
variable "bucket" { type = string }
variable "region" { type = string }
resource "google_storage_bucket" "distribution" {
  name                        = var.bucket
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  versioning { enabled = true }
  lifecycle { prevent_destroy = true }
}
resource "google_storage_bucket_iam_member" "downloads" {
  bucket = google_storage_bucket.distribution.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
output "bucket" { value = google_storage_bucket.distribution.name }
