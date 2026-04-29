# ============================================
# API Gateway Service
# ============================================
# Read-mostly REST API consumed by the React dashboard.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY services/api-gateway/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY shared/ ./shared/
COPY services/api-gateway/app/ ./app/

RUN useradd --system --uid 1001 --shell /usr/sbin/nologin gateway
USER gateway

ENV PYTHONPATH=/app

# uvicorn binds to API_HOST:API_PORT; defaults match Settings defaults
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]