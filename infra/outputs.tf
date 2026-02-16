output "cloud_run_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.main.uri
}

output "cloud_sql_connection_name" {
  description = "Cloud SQL connection name for proxy"
  value       = google_sql_database_instance.main.connection_name
}

output "cloud_sql_instance_ip" {
  description = "Cloud SQL instance public IP"
  value       = google_sql_database_instance.main.public_ip_address
}

output "storage_bucket_name" {
  description = "Cloud Storage bucket name"
  value       = google_storage_bucket.main.name
}

output "storage_bucket_url" {
  description = "Cloud Storage bucket URL"
  value       = "https://storage.googleapis.com/${google_storage_bucket.main.name}"
}

output "service_account_email" {
  description = "Cloud Run service account email"
  value       = google_service_account.cloudrun.email
}

output "db_user" {
  description = "Database username"
  value       = google_sql_user.app.name
}

output "db_name" {
  description = "Database name"
  value       = google_sql_database.main.name
}

# Deployment commands
output "deploy_commands" {
  description = "Commands to deploy the application"
  value       = <<-EOT

    # 1. Build and push container image
    gcloud builds submit --tag gcr.io/${var.project_id}/${var.app_name}:latest .

    # 2. Update Cloud Run with new image
    gcloud run services update ${var.app_name} \
      --image gcr.io/${var.project_id}/${var.app_name}:latest \
      --region ${var.region}

    # 3. Run migrations (using Cloud SQL Proxy locally)
    cloud-sql-proxy ${google_sql_database_instance.main.connection_name} &
    DB_HOST=localhost DB_PORT=5432 DB_NAME=${var.db_name} \
      DB_USER=${google_sql_user.app.name} DB_PASSWORD=<from-secret-manager> \
      python src/manage.py migrate

  EOT
}
