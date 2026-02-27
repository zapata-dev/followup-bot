FROM python:3.11-slim

WORKDIR /app/followup-bot

COPY followup-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY followup-bot/src/ src/

RUN mkdir -p /app/followup-bot/db

ENV PORT=10000
EXPOSE ${PORT}

CMD uvicorn src.main:app --host 0.0.0.0 --port ${PORT}
