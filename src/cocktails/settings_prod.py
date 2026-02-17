"""
Production settings for cocktails project on Google Cloud.

This file extends the base settings with production-specific configuration.
Secrets are loaded from Google Secret Manager.
"""

import os

from .settings import *  # noqa: F401, F403

# Add WhiteNoise middleware for static files (must be after SecurityMiddleware)
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405

# Security settings
DEBUG = False
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")

# HTTPS settings
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS settings
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Database - Cloud SQL via Unix socket or TCP
if os.environ.get("CLOUD_SQL_CONNECTION_NAME"):
    # Cloud Run with Cloud SQL Auth Proxy
    DATABASES["default"] = {  # noqa: F405
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": f'/cloudsql/{os.environ["CLOUD_SQL_CONNECTION_NAME"]}',
    }
else:
    # Direct TCP connection (for migrations, etc.)
    DATABASES["default"] = {  # noqa: F405
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }

# LLM Provider - use Gemini in production
LLM_PROVIDER = "gemini"
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# Storage configuration
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "cocktails-storage")

STORAGES = {
    # Media files (user uploads) - use GCS with public URLs (no signing)
    "default": {
        "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        "OPTIONS": {
            "bucket_name": GCS_BUCKET_NAME,
            "querystring_auth": False,  # Use public URLs, don't sign
        },
    },
    # Static files - use WhiteNoise (bundled with container)
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Static files - served by WhiteNoise from container
STATIC_URL = "/static/"
STATIC_ROOT = "/app/staticfiles"

# Media files URL - public GCS bucket
MEDIA_URL = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/"

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": (
                '{"time": "%(asctime)s", "level": "%(levelname)s", '
                '"name": "%(name)s", "message": "%(message)s"}'
            ),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "recipes": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "ingredients": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
