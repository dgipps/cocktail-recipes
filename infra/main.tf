provider "google" {
  project = var.project_id
  region  = var.region
}

# Enable required APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "generativelanguage.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}

# Service account for Cloud Run
resource "google_service_account" "cloudrun" {
  account_id   = "${var.app_name}-cloudrun"
  display_name = "Cloud Run Service Account for ${var.app_name}"

  depends_on = [google_project_service.apis]
}

# Random suffix for globally unique names
resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  db_instance_name = "${var.app_name}-db-${random_id.suffix.hex}"
  bucket_name      = "${var.app_name}-storage-${random_id.suffix.hex}"
}
