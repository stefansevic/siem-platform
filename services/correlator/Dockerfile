# ============================================
# Correlator Service
# ============================================
# Reads normalized_events from Redis, applies correlation rules,
# publishes detected incidents to the incidents stream.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY services/correlator/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY shared/ ./shared/
COPY services/correlator/app/ ./app/

RUN useradd --system --uid 1001 --shell /usr/sbin/nologin correlator
USER correlator

ENV PYTHONPATH=/app

CMD ["python", "-m", "app.main"]