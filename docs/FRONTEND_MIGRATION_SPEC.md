# JAMES OS — Frontend Migration Spec (re-skin, keep backend + DB)

**Goal:** rebuild the UI with a better design **without rebuilding any features**.
The features live in the Python backend + Supabase. The new frontend is a thin
client that calls the **same** API and reads/writes the **same** database.

> Golden rule: **do not touch `src/james_os/**`, `migrations/**`, Supabase, or any
> integration/env.** Only `web/` is being replaced. If you find yourself writing
> business logic, prompts, or SQL — stop; it already exists in the backend.

---

## 1. Architecture (what moves, what doesn't)

```
[ NEW frontend (your better design) ]   ← the only thing you build
            │  HTTPS, same-origin
            ▼
[ EXISTING FastAPI backend ]  ← unchanged  (Railway service: james-os-backend)
            │
            ▼
[ EXISTING Supabase Postgres + pgvector ]  ← unchanged
   project ref: scwxifchnhbrichinjsc
   James's tenant: 00000000-0000-0000-0000-000000000001 (slug "roy")
```

Every feature (memory/Ask, knowledge base, white papers, thesis→content→podcast,
content library/clipper, video engines, brand intake, strategy engine / Morning
Brief, analytics, etc.) is a backend endpoint. The new UI just calls it.

---

## 2. The 3 things the new frontend needs

### a) Point at the backend (same-origin proxy — NOT direct CORS)
Copy the `rewrites()` block from `web/next.config.mjs` into the new app and set
one env var:

```
BACKEND_ORIGIN = <the Railway URL of the james-os-backend service>
```
(Get the exact value from the current frontend's Railway env / Railway dashboard —
it's the `james-os-backend` service's public URL.)

**Why same-origin matters:** auth is a signed **session cookie**. If the new
frontend calls the backend on a *different* origin, the cookie won't be sent and
every request 401s. The existing app avoids this by proxying `/<api path>` →
`${BACKEND_ORIGIN}/<api path>` via Next rewrites, so the browser sees one origin
and the cookie is first-party. Replicate that. (Note: read `process.env` **inside**
the `rewrites()` function, not at module top level — Railway injects env after
module load.)

### b) Reuse the API client verbatim
`web/lib/api.ts` is the typed contract for **every** feature — copy it as-is into
the new app and build UI on top of `api.*`. It already encodes paths, methods,
request/response shapes. This is the single biggest time-saver.

### c) Auth flow (replicate, don't redesign the mechanism)
- `POST /auth/signup` `{ email, password, display_name? }`
- `POST /auth/login`  `{ email, password }`  → sets the session cookie
- `POST /auth/logout`
- `POST /auth/password` `{ current_password, new_password }`

Build `/login` + `/signup` screens that call these; after login, all other calls
just work (cookie carried same-origin). Invited signups auto-provision a fresh
isolated tenant server-side — no client work.

---

## 3. Source of truth for the full API

Two authoritative references — no manual endpoint list to maintain:
1. **`GET {BACKEND_ORIGIN}/openapi.json`** — FastAPI auto-generates the complete,
   always-current schema for every endpoint (params + response models). Point the
   other session at this to know everything.
2. **`web/lib/api.ts`** — the same surface, already typed in TypeScript.

Top-level API path families to proxy (from `next.config.mjs` — copy the whole list):
`/ask · /events · /ingest · /generate* · /post · /research · /video · /autopilot ·
/trends · /media · /images · /hero · /long-form · /api · /auth · /agent ·
/analytics · /integrations · /voice · /templates · /compositions · /changes ·
/higgsfield · /brand-kit · /knowledge · /content-library · /speakers ·
/brand-profile · /suggestions · /intake · /strategy · /health`

---

## 4. Screen inventory (what to redesign)

37 pages today. Group them however your new design wants; the routes below map to
the api.ts calls, so a screen = "call these endpoints, render nicely."

**Core / memory**
- `/` — Ask the memory (grounded, cited Q&A) → `api` ask/events
- `/brief` — Morning Brief: weekly Prescription + playbooks + queue snapshot → `strategyState / prescribeNow / refreshStrategyInputs / acceptPrescription`
- `/updates` — changelog → `/changes`

**Brand brain**
- `/brand` — the brain (guardrails, voice corpus, memory counts)
- `/intake` — brand setup + agentic intake (research-my-brand, deep interview) → `/intake/*`, `/brand-profile`
- `/voice-studio`, `/audio` — voice corpus ingest → `ingestVoiceDrive / voiceJobs / voiceCorpus`

**Knowledge**
- `/knowledge` — upload docs + Ask-your-docs → `/knowledge/*`
- `/library` — corpus browse
- `/thesis` — Weekly Thesis → papers → content → podcast → `/knowledge/thesis/*`

**Content production**
- `/create` — one front door → routes to engines
- `/content-library` — topics + build → `/content-library/*`
- `/queue`, `/pipeline` — Approval Queue + lifecycle → `listProductions / approve/reject/cancel/trim/deleteProduction`
- `/autopilot` — autopilot config/runs → `getAutopilotConfig / runAutopilot / listAutopilotRuns / bulkGenerate`
- Video engines: `/video`, `/long-form`, `/jp-clips`, `/jp-live`, `/engaging-video`,
  `/story-video`, `/story-mix`, `/heygen-video`, `/hero`, `/editor`, `/preview`,
  `/broll` → `/video/*`, `/long-form/*`, `/hero/*`
- Images/design: `/images`, `/design-studio`, `/style-templates` → `/images/*`, `/templates`
- `/style-templates` / caption+image styles → `listCaptionStyles / listImageStyles`

**Research / distribution / analytics**
- `/market-research`, `/social-listening`, `/social-companion` → `/research/*` (Xpoz search/trending/save/draft)
- `/analytics` — live + historical → `analytics*`
- `/settings`, `/profile` — connections, brand accounts, credentials → `listConnections / listBrandAccounts / setBrandAccounts`

**Auth**
- `/login`, `/signup` → `/auth/*`

(Exact per-page endpoint sets: grep each `web/app/<route>/page.tsx` in the current
app for `api.` — that's the definitive list per screen.)

---

## 5. Gotchas to carry over

- **202 + poll pattern:** some endpoints kick off background work and return
  immediately (e.g. `prescribeNow`, `refreshStrategyInputs`, video produce). The UI
  polls `…/state` or the resource until done. Preserve this; don't `await` a
  single long request.
- **Media URLs** come from the backend/Supabase Storage (`supabase_media_bucket`);
  render them as given, don't re-host.
- **Multi-tenant:** the backend scopes everything to the logged-in tenant via RLS.
  The frontend never sends a tenant id — it's derived from the session. Don't add one.
- **Nothing auto-publishes:** the Approval Queue gate is intentional. Keep a human
  approve/reject step for generated content.

---

## 6. Migration checklist (for the other session)

1. Set `BACKEND_ORIGIN` to the james-os-backend Railway URL.
2. Copy `web/next.config.mjs` `rewrites()` (same-origin proxy) into the new app.
3. Copy `web/lib/api.ts` as the API client.
4. Build `/login` + `/signup`, confirm a logged-in request (e.g. `api.health()`
   then an authed call) returns 200 with the cookie.
5. Rebuild screens from §4, newest/most-used first (`/`, `/brief`, `/queue`,
   `/create`, `/knowledge`).
6. For anything unclear, read `{BACKEND_ORIGIN}/openapi.json`.
7. Deploy the new frontend as a separate Railway service pointing at the same
   backend. **Do not** redeploy or modify the backend.

---

## 7. Copy-paste kickoff prompt for the other session

> I'm building a new, better-designed frontend for an existing app called JAMES OS.
> **Do not build any backend features or database logic — they already exist and must
> not be touched.** The existing FastAPI backend + Supabase are staying exactly as
> they are; I'm only rebuilding the UI (the `web/` layer) with a better design,
> pointed at the same backend and same database.
>
> Setup:
> - Backend: reachable via same-origin Next.js rewrites to `BACKEND_ORIGIN` (the
>   `james-os-backend` Railway service). Auth is a session cookie, so the frontend
>   MUST proxy the API same-origin (copy the `rewrites()` block and the API client
>   from the old app's `web/next.config.mjs` and `web/lib/api.ts`).
> - The full API is at `{BACKEND_ORIGIN}/openapi.json`; the typed client is
>   `web/lib/api.ts`.
> - Auth endpoints: `/auth/signup`, `/auth/login`, `/auth/logout`.
>
> Task: using my new design system, rebuild these screens as thin clients over the
> existing `api.*` calls (start with `/`, `/brief`, `/queue`, `/create`,
> `/knowledge`): [paste the screen list from §4]. Preserve the 202+poll pattern for
> long-running actions and the human Approval Queue gate. Never send a tenant id —
> it's derived from the session server-side.
