FROM python:3.11-slim

WORKDIR /app

# Prvo zavisnosti (bolje keširanje Docker slojeva pri rebuild-u)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Zatim kod aplikacije
COPY app/ ./app/

EXPOSE 9000

# Pokreni preko uvicorn-a (demo podešavanja)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000", "--access-log"]