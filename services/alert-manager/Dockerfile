# ============================================
# Alert Manager Service
# ============================================
# Reads incidents from Redis, deduplicates, persists to Postgres,
# notifies via console and optionally webhook.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY services/alert-manager/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY shared/ ./shared/
COPY services/alert-manager/app/ ./app/

RUN useradd --system --uid 1001 --shell /usr/sbin/nologin alertmanager
USER alertmanager

ENV PYTHONPATH=/app

CMD ["python", "-m", "app.main"]