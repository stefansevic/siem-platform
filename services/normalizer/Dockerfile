# ============================================
# Normalizer Service
# ============================================
# Reads raw_logs from Redis, parses+maps to ECS, persists to Postgres,
# republishes to normalized_events for the Correlation Engine.

FROM python:3.11-slim

# Avoid .pyc clutter and ensure stdout/stderr are unbuffered
# (so Docker logs reflect real-time service activity).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first (separate layer for better caching)
COPY services/normalizer/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Copy shared models (Pydantic ECS schemas, Redis key constants)
COPY shared/ ./shared/

# Copy the service code
COPY services/normalizer/app/ ./app/

# Run as non-root for defense-in-depth
RUN useradd --system --uid 1001 --shell /usr/sbin/nologin normalizer
USER normalizer

# PYTHONPATH=/app so that 'from shared...' and 'from app...' resolve
ENV PYTHONPATH=/app

# Entry point: the run_service() coroutine in main.py
CMD ["python", "-m", "app.main"]
