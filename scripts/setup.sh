#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[PaperPilot] Copying .env (if missing)"
cp -n .env.example .env 2>/dev/null || true

echo "[PaperPilot] Building and starting services"
docker compose up -d --build

echo "[PaperPilot] Running migrations"
make migrate

echo
echo "[PaperPilot] Ready"
echo "  Frontend: http://localhost:5173"
echo "  Backend:  http://localhost:8000/health"
echo
echo "[PaperPilot] First-time tip: open Settings (gear icon) and set your LLM key."

