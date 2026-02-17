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

# Copy virtual environment from builder (with correct ownership)
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Set PATH to use virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy application code (with correct ownership)
COPY --chown=appuser:appuser src/ ./src/

# Set Django settings module
ENV DJANGO_SETTINGS_MODULE=cocktails.settings_prod
ENV PYTHONPATH=/app/src

# Create static files directory
RUN mkdir -p /app/staticfiles && chown appuser:appuser /app/staticfiles

# Collect static files (using dummy secrets for build - WhiteNoise needs this)
RUN DJANGO_SECRET_KEY=build-secret \
    DB_NAME=x DB_USER=x DB_PASSWORD=x \
    GEMINI_API_KEY=x \
    python src/manage.py collectstatic --noinput

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8080

# Run gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "120", "cocktails.wsgi:application"]
