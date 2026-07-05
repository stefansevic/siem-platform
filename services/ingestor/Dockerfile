# ============================================
# SIEM Platforma - Ingestor servis
# ============================================
# Build kontekst je koren repo-a, da bismo mogli da kopiramo `shared/`
# zajedno sa kodom servisa.

FROM python:3.11-slim AS base

# Brže, manje, bez .pyc fajlova
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Prvo zavisnosti, radi boljeg keširanja Docker slojeva
COPY services/ingestor/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Zatim shared kod i kod servisa
COPY shared/ ./shared/
COPY services/ingestor/app/ ./app/

# Pokreni kao non-root korisnik (bezbednije)
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

# Jedan uvicorn worker je dovoljan za našu skalu; u prod koristiti --workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]