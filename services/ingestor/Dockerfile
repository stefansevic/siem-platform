# ============================================
# SIEM Platform - Ingestor Service
# ============================================
# Build context is the repository root so we can copy `shared/`
# alongside the service code.

FROM python:3.11-slim AS base

# Faster, smaller, no .pyc files
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better Docker layer caching
COPY services/ingestor/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared code and service code
COPY shared/ ./shared/
COPY services/ingestor/app/ ./app/

# Run as non-root user
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

# uvicorn with one worker is fine for our scale; use --workers in prod
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]