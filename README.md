# JAMES OS — memory substrate

The substrate that the rest of JAMES OS sits on. Everything you put in stays,
queryable, forever. Every AI answer is grounded in real events and refuses
when it can't be.

This repo is the Python backend (FastAPI). The Next.js dashboard lives in a
sibling repo (TBD).

## What's in here

```
james-os/
├── migrations/                # SQL schema (auto-applied by Postgres on first boot)
├── src/james_os/
│   ├── main.py                # FastAPI app: /events, /plug-ins, /ask, /health
│   ├── ask.py                 # The cite-or-refuse pipeline
│   ├── retrieval.py           # Hybrid search (vector + full-text)
│   ├── rerank.py              # Cohere reranker (optional, falls back to truncate)
│   ├── ingestion.py           # Embed + insert events, idempotent
│   ├── embedder.py            # Pluggable embedding providers (Voyage, stub)
│   ├── llm.py                 # Pluggable LLM providers (Anthropic, stub)
│   ├── prompts.py             # System-prompt assembly from plug_ins table
│   ├── adapters/              # Ingestion adapters (manual now; more later)
│   ├── models.py              # Pydantic models
│   ├── db.py                  # Async pool with per-request RLS tenant scoping
│   └── config.py              # Settings (env-driven)
├── tests/                     # Pytest, runs against local Docker Postgres
├── docker-compose.yml         # Postgres + pgvector locally
└── pyproject.toml
```

## Quick start

```bash
# 1. Install uv (fast Python package manager) if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Create venv and install
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"

# 3. Start Postgres (applies migrations automatically on first boot)
docker compose up -d

# 4. Copy env template
cp .env.example .env

# 5. Run tests (uses stub providers, no API keys needed)
.venv/bin/pytest -v

# 6. Run the API
.venv/bin/uvicorn james_os.main:app --reload
```

API runs on `http://localhost:8000` with auto-generated docs at `/docs`.

> Postgres is mapped to host port **5433** (not the default 5432) to avoid
> conflicting with any host-installed Postgres. Update `DATABASE_URL` in `.env`
> if you change this.

## Try it

```bash
# Capture an event
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "decision",
    "payload": {"text": "Pricing for Spaceport pilot is $500K for 90 days"},
    "raw_content": "Pricing for Spaceport pilot is $500K for 90 days",
    "source": {"adapter": "manual", "dedupe_key": "decision-spaceport-pricing-1"}
  }'

# List events
curl http://localhost:8000/events

# Ask the memory (refuses by default with stub LLM)
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What did we decide about Spaceport pricing?"}'
```

## Switching from stub providers to real ones

In `.env`:

```bash
EMBEDDING_PROVIDER=voyage
VOYAGE_API_KEY=<your key>

LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<your key>

# Optional but recommended
COHERE_API_KEY=<your key>
```

Restart the API and you're using production providers.

## What this implements (and what's deliberately not in v0.1)

In:

- The full data model (events, plug_ins, adapters, queries, actions, outbox).
- Multi-tenant RLS at the database level.
- Idempotent ingestion via dedupe_key.
- Hybrid retrieval (vector + full-text), parallel fan-out, score-based merge.
- Cohere reranking (graceful no-op if no key).
- Cite-or-refuse generation with verification pass.
- Every query and answer logged to the `queries` table for audit.

Not yet:

- Knowledge-graph index (planned phase 2; vector + FTS + structured covers v1).
- Background workers / Inngest integration (planned Week 2).
- Real ingestion adapters beyond manual (Gmail, Drive, voice memo, YouTube — Week 4 onward).
- Approval queue execution (UI exists in the dashboard repo; API extension coming Week 4).
- Verification of large answers (current verifier is single-pass; chunked verification for long outputs is a v2 hardening item).

## How the architecture defends against hallucination

Five layers, in order:

1. **Retrieval-grounded inputs.** The LLM only ever sees retrieved events,
   never asked to "remember" anything from training.
2. **Cite-or-refuse prompt.** Every claim must reference an event_id from
   the retrieval set. If it can't, it must refuse.
3. **Verification pass.** A second LLM call checks each claim against the
   cited source. Any unverified claim → refuse.
4. **Rules-as-data.** The system prompt is rebuilt from the `plug_ins`
   table on every call, so user input cannot override the rules.
5. **Action gates.** Anything that *does* something in the world (vs just
   answers a question) routes through the approval queue (TODO: wire up).

See `prompts.py` and `ask.py` for the implementation.

## A note on the parent folder

The folder `/Users/royantony/BRAND MANAGER /` has a trailing space in its
name, which can confuse some shell tools. Consider renaming it to
`brand-manager` (no spaces) once the team is settled. The code itself is
inside `james-os/` and unaffected.
