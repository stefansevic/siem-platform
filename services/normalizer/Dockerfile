# ============================================
# Normalizer servis
# ============================================
# Čita raw_logs iz Redis-a, parsira i mapira u ECS, upiše u Postgres,
# i objavi u normalized_events za Correlator.

FROM python:3.11-slim

# Bez .pyc smeća i sa nebaferovanim stdout/stderr
# (da Docker logovi prikazuju aktivnost u realnom vremenu).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Prvo zavisnosti (odvojen sloj radi boljeg keširanja)
COPY services/normalizer/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Zajednički modeli (Pydantic ECS šeme, konstante Redis ključeva)
COPY shared/ ./shared/

# Kod servisa
COPY services/normalizer/app/ ./app/

# Pokreni kao non-root, radi dodatne bezbednosti
RUN useradd --system --uid 1001 --shell /usr/sbin/nologin normalizer
USER normalizer

# PYTHONPATH=/app da bi 'from shared...' i 'from app...' radili
ENV PYTHONPATH=/app

# Ulazna tačka: run_service() korutina iz main.py
CMD ["python", "-m", "app.main"]
