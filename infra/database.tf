# Cloud SQL PostgreSQL Instance
resource "google_sql_database_instance" "main" {
  name             = local.db_instance_name
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL" # Single zone for cost savings
    disk_size         = 10      # GB, minimum

    # Enable pg_trgm extension support
    database_flags {
      name  = "cloudsql.enable_pg_cron"
      value = "off"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = false # Disable for cost savings
      start_time                     = "03:00"
      transaction_log_retention_days = 1
      backup_retention_settings {
        retained_backups = 7
      }
    }

    ip_configuration {
      ipv4_enabled = true
      # Cloud Run connects via Cloud SQL Auth Proxy, no need for authorized networks
    }

    maintenance_window {
      day  = 7 # Sunday
      hour = 3 # 3 AM
    }
  }

  deletion_protection = true

  depends_on = [google_project_service.apis]
}

# Database
resource "google_sql_database" "main" {
  name     = var.db_name
  instance = google_sql_database_instance.main.name
}

# Database user
resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "google_sql_user" "app" {
  name     = "${var.app_name}_user"
  instance = google_sql_database_instance.main.name
  password = random_password.db_password.result
}

# IAM binding for Cloud Run to connect to Cloud SQL
resource "google_project_iam_member" "cloudrun_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.cloudrun.email}"
}
