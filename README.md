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

## Agent Skills

PaperPilot indexes the
[Orchestra Research AI skill library](https://github.com/Orchestra-Research/AI-research-SKILLs)
at backend startup. It recursively discovers `SKILL.md` files, records the resolved
Git commit, and keeps only validated routing metadata in memory. Skill selection
uses that metadata without reading any body. When a research run begins, only its
selected skill bodies are opened from the pinned local snapshot, revalidated, and
placed in a globally bounded LRU cache. No repository or network access occurs in
an agent turn.
The source cache retains at most eight immutable revisions, matching the
in-process registry history used by pinned agent runs.

Skills are treated as untrusted reference material. They cannot register tools,
run scripts, install their declared dependencies, change permissions, or override
PaperPilot's evidence and safety rules. The upstream `autoresearch` orchestration
skill is excluded from automatic selection by default because it contains
platform-control instructions.

Open **Skills** in the workspace sidebar to preview metadata-only task routing,
search and filter the marketplace catalog, inspect a skill, and monitor the
number of available versus actually loaded skill bodies. A preview never warms
the body cache.

Useful endpoints:

- `GET /api/skills/status` — loader state, active revision, and diagnostics
- `GET /api/skills` — active metadata catalog (never raw skill bodies)
- `GET /api/skills/{name}` — metadata for one skill
- `POST /api/skills/preview` — explain metadata-only selection for a task and flow

Configure the loader with the `AGENT_SKILLS_*` variables in `.env.example`.
`AGENT_SKILLS_CACHE_MAX_ENTRIES` and `AGENT_SKILLS_CACHE_MAX_BYTES` bound the
lazy document cache in each API process; `AGENT_SKILLS_MAX_REFERENCE_BYTES`
bounds explicit, manifest-listed Markdown reference reads for an already
selected skill. Status cache counters are likewise process-local.
For reproducible production deployments, set `AGENT_SKILLS_REPO_REF` to a
reviewed 40-character commit SHA. If a refresh fails, PaperPilot keeps the last
validated local snapshot and continues without making network calls in an agent
turn.

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
