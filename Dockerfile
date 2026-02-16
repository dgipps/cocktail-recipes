# Build stage
FROM python:3.13-slim AS builder

WORKDIR /app

# Install uv for fast package installation
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies (including production extras)
RUN uv sync --frozen --no-dev --extra prod

# Production stage
FROM python:3.13-slim

WORKDIR /app

# Create non-root user
RUN useradd --create-home appuser

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Set PATH to use virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy application code
COPY src/ ./src/

# Set Django settings module
ENV DJANGO_SETTINGS_MODULE=cocktails.settings_prod
ENV PYTHONPATH=/app/src

# Collect static files (will be uploaded to GCS on deploy)
# This step requires env vars, so we skip it in build and do it in entrypoint
# RUN python src/manage.py collectstatic --noinput

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8080

# Run gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "120", "cocktails.wsgi:application"]
