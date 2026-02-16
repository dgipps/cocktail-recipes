variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "northamerica-northeast1"
}

variable "app_name" {
  description = "Application name used for resource naming"
  type        = string
  default     = "cocktails"
}

# Database
variable "db_tier" {
  description = "Cloud SQL instance tier"
  type        = string
  default     = "db-f1-micro" # Smallest, ~$10/month
}

variable "db_name" {
  description = "PostgreSQL database name"
  type        = string
  default     = "cocktails"
}

# Cloud Run
variable "cloud_run_min_instances" {
  description = "Minimum number of Cloud Run instances (0 for scale-to-zero)"
  type        = number
  default     = 0
}

variable "cloud_run_max_instances" {
  description = "Maximum number of Cloud Run instances"
  type        = number
  default     = 2
}

variable "container_image" {
  description = "Container image to deploy (e.g., gcr.io/PROJECT/cocktails:latest)"
  type        = string
  default     = ""
}

# Secrets (sensitive - provide via tfvars or environment)
variable "django_secret_key" {
  description = "Django SECRET_KEY"
  type        = string
  sensitive   = true
}

variable "gemini_api_key" {
  description = "Google Gemini API key"
  type        = string
  sensitive   = true
}

# Domain (optional)
variable "domain" {
  description = "Custom domain for Cloud Run (optional)"
  type        = string
  default     = ""
}
