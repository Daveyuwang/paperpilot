#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

cat <<'EOF'
[PaperPilot] No-Docker setup (local dev)

Note: This project has native deps (PyMuPDF, libpq, etc). Docker is the easiest path.
If you still want no-Docker, we recommend using conda for Python + system libs.

Suggested steps:

  conda create -n paperpilot -y -c conda-forge python=3.11 nodejs=20
  conda activate paperpilot

  # Install OS/system deps as needed (poppler, libpq, etc) depending on your OS.

  cd backend
  # If you prefer strict conda-only installs, install packages from conda-forge.
  # Otherwise, pip is the practical fallback for the full requirements set:
  pip install -r requirements.txt

  alembic upgrade head
  uvicorn app.main:app --reload
  celery -A app.ingestion.celery_app worker -l info

  cd ../frontend
  npm install
  npm run dev

EOF

