#!/bin/sh
set -eu

if [ -n "${DATABASE_URL:-}" ]; then
  uv run --no-sync alembic upgrade head
fi

exec uv run --no-sync uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers
