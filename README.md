# Cocktails

A Django app for browsing cocktail recipes, managing your home bar inventory, and discovering what you can make with what you have on hand. Supports importing recipes from images using LLM-powered OCR and parsing.

## Architecture overview

```
src/
├── cocktails/          # Project package: settings, root URLs, WSGI
├── recipes/            # Recipe browsing and detail views
├── ingredients/        # Ingredient and category management
└── inventory/          # Per-user ingredient stock tracking

infra/                  # Terraform (Google Cloud)
tests/                  # pytest test suite
Dockerfile              # Multi-stage production build
pyproject.toml          # Dependencies (managed with uv)
```

### Django apps

| App | Responsibility |
|-----|---------------|
| **recipes** | `Recipe` and `RecipeIngredient` models; list, detail, and "available" views |
| **ingredients** | `Ingredient` and `IngredientCategory` models; hierarchical categories via a closure table (`IngredientCategoryAncestor`) |
| **inventory** | `UserInventory` model; tracks which ingredients each user has in stock |

### URL structure

| URL | View |
|-----|------|
| `/recipes/` | All recipes, with name search and category filter |
| `/recipes/available/` | Recipes makeable from the user's inventory |
| `/recipes/<slug>/` | Recipe detail |
| `/admin/` | Django admin |

### LLM integration

The app can import recipes from photos. Two providers are supported, selected via `settings.LLM_PROVIDER`:

- **Ollama** (default in development) — runs locally; uses `minicpm-v` for OCR and `llama3.2` for parsing.
- **Gemini** (production) — Google Gemini API; used automatically when `settings_prod.py` is active.

---

## Development

### Prerequisites

- Python 3.13
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- PostgreSQL running locally

### Setup

```bash
# Clone and enter the project
git clone <repo-url>
cd cocktails

# Install dependencies (creates .venv automatically)
uv sync

# Configure the database
# Default: postgres://localhost/cocktails
# Override with env vars: DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
createdb cocktails

# Apply migrations
uv run python src/manage.py migrate

# Create a superuser
uv run python src/manage.py createsuperuser

# Run the dev server
uv run python src/manage.py runserver
```

The app will be available at http://127.0.0.1:8000/recipes/.

### Running tests

```bash
uv run pytest
```

### Linting

```bash
uv run ruff check .
uv run ruff format .
```

### Environment variables (development)

All have sensible defaults for local development. Override as needed:

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_NAME` | `cocktails` | PostgreSQL database name |
| `DB_USER` | `""` | Database user |
| `DB_PASSWORD` | `""` | Database password |
| `DB_HOST` | `localhost` | Database host |
| `DB_PORT` | `5432` | Database port |
| `LLM_PROVIDER` | `ollama` | `ollama` or `gemini` |
| `OLLAMA_OCR_MODEL` | `minicpm-v` | Vision model for OCR |
| `OLLAMA_PARSE_MODEL` | `llama3.2` | Text model for parsing |
| `GEMINI_API_KEY` | — | Required if `LLM_PROVIDER=gemini` |

---

## Deployment

The app runs on **Google Cloud Run** backed by **Cloud SQL (PostgreSQL 15)** and **Cloud Storage** for media files. Infrastructure is managed with Terraform.

### Infrastructure (first-time setup)

```bash
cd infra

# Create terraform.tfvars with your values:
cat > terraform.tfvars <<EOF
project_id        = "your-gcp-project-id"
django_secret_key = "your-secret-key"
gemini_api_key    = "your-gemini-api-key"
EOF

terraform init
terraform apply
```

This provisions:
- Cloud Run service (scales to zero, max 2 instances, 1 CPU / 512 MB)
- Cloud SQL PostgreSQL 15 (db-f1-micro, daily backups)
- Cloud Storage bucket for media (public read, 365-day auto-delete)
- Google Secret Manager secrets for sensitive config
- Service account with least-privilege IAM roles

### Building and deploying

```bash
# Build and push the image
gcloud builds submit --tag gcr.io/<PROJECT_ID>/cocktails:latest .

# Deploy to Cloud Run
gcloud run services update cocktails \
  --image gcr.io/<PROJECT_ID>/cocktails:latest \
  --region northamerica-northeast1
```

### Running migrations in production

Use the Cloud SQL Auth Proxy to connect from your local machine:

```bash
# Start the proxy (keep running in background)
cloud-sql-proxy <CONNECTION_NAME> &

# Run migrations
DJANGO_SETTINGS_MODULE=cocktails.settings_prod \
  uv run python src/manage.py migrate
```

The Cloud SQL connection name is output by Terraform as `cloud_sql_connection_name`.

### Production settings

`src/cocktails/settings_prod.py` enables:
- `DEBUG = False`
- HTTPS redirect and HSTS
- Secure cookies
- WhiteNoise for static files
- Google Cloud Storage for media
- Gemini as the LLM provider
- JSON-formatted logging to stdout (consumed by Cloud Logging)

Secrets (`SECRET_KEY`, `DB_PASSWORD`, `GEMINI_API_KEY`) are injected at runtime from Google Secret Manager via Cloud Run environment variables.
