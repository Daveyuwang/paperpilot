# PaperPilot

AI-powered research workspace for scholars. Upload PDFs, ask grounded questions with hybrid RAG, discover sources, manage structured deliverables, and run bounded deep research sessions.

## Quick Start (Docker)

```bash
cp .env.example .env
make setup
```

Frontend: `http://localhost:5173`
Backend: `http://localhost:8000`

## Local Dev (No Docker)

Requires: Python 3.11+, Node 20+, PostgreSQL, Redis, Qdrant running locally.

```bash
cp .env.example .env

cd backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
celery -A app.ingestion.celery_app worker -l info

cd ../frontend
npm install
npm run dev
```

## Configure LLM (in-app)

Open `Settings` to configure:
- Protocol: Anthropic / OpenAI / OpenAI-compatible / Gemini
- Model: e.g. `claude-sonnet-4-6`
- API key: stored server-side per guest (Redis TTL)

## Make Commands

```
make setup       Build, start, and migrate (one-shot)
make up          Start all services
make down        Stop all services
make migrate     Run Alembic migrations
make reset-db    Drop and recreate database
make logs        Tail all service logs
make test-backend  Run backend tests
```