FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

EXPOSE 9000

# Run with uvicorn (production-ish settings for demo)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000", "--access-log"]