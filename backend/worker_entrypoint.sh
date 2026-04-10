#!/bin/bash
set -e

# Wait for Postgres and Redis to be ready before starting worker
echo "Waiting for Postgres..."
until python -c "import psycopg2; psycopg2.connect('$DATABASE_URL_SYNC')" 2>/dev/null; do
  sleep 1
done

echo "Starting Celery worker..."
exec celery -A app.ingestion.celery_app worker \
  --loglevel=info \
  --concurrency=2 \
  -Q ingestion,default
