# Cloud Storage bucket for static files and media
resource "google_storage_bucket" "main" {
  name     = local.bucket_name
  location = var.region

  # Use Standard storage class (cheapest for frequently accessed)
  storage_class = "STANDARD"

  # Enable uniform bucket-level access
  uniform_bucket_level_access = true

  # CORS for web access
  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Cache-Control"]
    max_age_seconds = 3600
  }

  # Lifecycle rule to clean up old uploads (optional)
  lifecycle_rule {
    condition {
      age = 365 # Days
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

# Make bucket publicly readable for static files
resource "google_storage_bucket_iam_member" "public_read" {
  bucket = google_storage_bucket.main.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# Allow Cloud Run service account to write to bucket
resource "google_storage_bucket_iam_member" "cloudrun_write" {
  bucket = google_storage_bucket.main.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.cloudrun.email}"
}
