FROM python:3.11-slim

WORKDIR /app/followup-bot

# System dependencies for PostgreSQL / Cloud SQL connector
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY followup-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY followup-bot/src/ src/

ENV PORT=8080
EXPOSE 8080

CMD uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8080}
