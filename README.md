# PaperPilot

Agent-driven research assistant for PDFs: hybrid RAG, intent-routed multi-step LLM pipeline, evidence-grounded streaming Q&A, session memory, and structured concept maps.

**Guided reading trail** — staged questions across Motivation, Approach, Experiments, and Takeaways.

![Guided reading trail](screenshots/trail.png)

**Structured answers** — evidence-grounded responses with citations and streaming.

![Structured answer](screenshots/ans-1.png)

![Structured answer (detail)](screenshots/ans-2.png)

**Concept map** — LLM-generated graph of concepts and relations grounded in the paper.

![Concept map](screenshots/concept.png)

**Prerequisites:** Docker with Compose · for local dev without Docker: Node 20+, Python 3.11+

## Local (Docker)

```bash
cp .env.example .env

make up
make migrate
```

## Production (Compose)

```bash
cp .env.example .env
# production: DATABASE_*, REDIS_*, QDRANT_*, SECRET_KEY, CORS_ORIGINS, LLM_PROVIDER_API_KEY, …

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
make migrate   # or: docker compose exec backend alembic upgrade head
```

Frontend: set `VITE_API_URL` / `VITE_WS_URL` at build time if API is not same-origin.

## Testing & CI

```bash
make test-backend
```

`.github/workflows/ci.yml` — frontend build + backend tests on push/PR to `main` / `master`.

## Render

1. Edit `render.yaml`: replace `YOUR-API` / `YOUR-FRONTEND`; adjust service names if needed.  
2. Render Dashboard → **Blueprints** → New Blueprint Instance → pick repo → apply.  
3. Dashboard env (secrets): `ANTHROPIC_API_KEY`, `SECRET_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`, plus anything else not from linked Postgres/Redis.

## Dev (no Docker)

```bash
# .env → local Postgres, Redis, Qdrant URLs

cd backend && pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
celery -A app.ingestion.celery_app worker -l info

cd ../frontend && npm install && npm run dev
```