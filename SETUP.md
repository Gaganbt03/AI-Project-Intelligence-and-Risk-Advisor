# Setup Guide — AI Project Intelligence & Risk Advisor

This guide walks through a fresh install on **Windows** (the primary target) with notes for
macOS / Linux.

## 1. Prerequisites

| Tool     | Version    | Notes                                            |
|----------|------------|--------------------------------------------------|
| Python   | 3.11+      | Developed on 3.13.3                              |
| Node.js  | 20+        | Vite requires Node 18+, tested on 22             |
| Ollama   | any recent | Local LLM runtime. Required for the primary provider **and for all embeddings**. |

### 1.1 Pull the AI models

Ollama must be running before you start the backend:

```bash
ollama pull qwen2.5:3b          # primary chat/generation model
ollama pull nomic-embed-text    # embedding model (kept separate from the LLM)
```

Verify with `ollama list` — you should see both models.

> **Embeddings always stay local.** `nomic-embed-text` on Ollama produces the 768-dimension
> vectors used by retrieval. They are never computed by Groq, Gemini, OpenRouter, Hugging
> Face or any other cloud provider — the fallback chain applies to generation only.

## 2. Backend

```bash
cd backend

# 2.1 Create a virtual environment
python -m venv .venv
.venv\Scripts\activate          # PowerShell (Windows)
# source .venv/bin/activate     # macOS / Linux

# 2.2 Install dependencies
pip install -r requirements.txt

# 2.3 Configure environment
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux

# 2.4 (Optional) Bootstrap the first admin from the environment
# Edit backend\.env and set:
#   ADMIN_EMAIL=you@company.com
#   ADMIN_PASSWORD=YourStrongPassword
#   ADMIN_NAME=Your Name
# Only used when the database has zero users.

# 2.5 Start the API
python run.py
```

The API is now at http://127.0.0.1:8000 — Swagger docs at http://127.0.0.1:8000/docs.
First launch creates the SQLite database, uploads and vector store under `backend/data/`.

> **No demo data.** Nothing is seeded. You create real projects and employees through the app.

## 3. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to `http://127.0.0.1:8000`, so CORS is a
non-issue in development.

For a production-style check:

```bash
npm run build        # type-checks (tsc) and bundles into frontend/dist
```

## 4. First run

1. Open http://localhost:5173. The app detects an empty database and shows the
   **Create administrator** screen (unless you bootstrapped via environment).
2. Create the administrator account and log in.
3. Go to **Projects → Create Project** (add team members later or immediately).
4. On **Employees**, add team members and assign them to projects.
5. Inside a project, **Documents** — upload PDF/DOCX/CSV/TXT files.
   Processing + embedding happens automatically; files show a status.
6. **AI Insights → Run Analysis** — the five agents run and results appear as structured
   cards backed by source citations.
7. Try the **Assistant** tab — ask a question; the answer shows its grounding sources.
8. Optional: **Settings → AI Providers** (admin) — add a Groq, Gemini, OpenRouter or
   Hugging Face API key to switch generation to a cloud provider when Ollama is unavailable.
   The page lists the chain in order and lets you test each slot; keys are typed there and
   stored server-side.

## 5. Configuration reference (`backend/.env`)

| Variable                          | Default                        | Purpose                                        |
|-----------------------------------|--------------------------------|------------------------------------------------|
| `DATABASE_URL`                    | `sqlite:///./data/app.db`      | SQLite file (swap for Postgres later)         |
| `SECRET_KEY`                      | `change-me...`                 | JWT signing secret — **change in prod**       |
| `ACCESS_TOKEN_EXPIRE_MINUTES`     | `720`                          | Token lifetime                                |
| `CORS_ORIGINS`                    | `http://localhost:5173`        | Comma-separated allowed origins               |
| `MAX_UPLOAD_MB`                   | `25`                           | Upload size cap                               |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL`| `http://localhost:11434`/`qwen2.5:3b` | Primary AI provider           |
| `EMBEDDING_PROVIDER`/`MODEL`      | `ollama`/`nomic-embed-text`    | Embeddings provider (separate from LLM)       |
| `CHUNK_SIZE` / `CHUNK_OVERLAP`    | `1000` / `150`                 | Chunking tokens; also editable in Settings UI |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD`  | blank                          | First-admin bootstrap; remove after use       |

Chunk size, overlap, max chunks and temperature are also editable at runtime under
**Settings → AI Settings** (admin).

### 5.1 AI provider chain

Generation uses a **deterministic, sequential** fallback chain. Providers are tried one at a
time — never in parallel — and the first that returns a valid answer is reported back to the
UI. A provider is skipped when it is unconfigured, unreachable, times out, or returns an
error.

| Order | Provider         | Role         | Variables |
|-------|------------------|--------------|-----------|
| 1     | Ollama           | Primary      | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` — no API key |
| 2     | Groq             | Fallback 1   | `GROQ_BASE_URL`, `GROQ_API_KEY`, `GROQ_MODEL` |
| 3     | Gemini           | Fallback 2   | `GEMINI_BASE_URL`, `GEMINI_API_KEY`, `GEMINI_MODEL` |
| 4     | OpenRouter       | Fallback 3   | `EXTERNAL_PROVIDER_1_NAME/BASE_URL/API_KEY/MODEL` |
| 5     | Hugging Face     | Fallback 4   | `EXTERNAL_PROVIDER_2_NAME/BASE_URL/API_KEY/MODEL` |
| 6     | OpenAI-Compatible| Extra        | `EXTERNAL_PROVIDER_3_NAME/BASE_URL/API_KEY/MODEL` |

`EXTERNAL_PROVIDER_3_*` remains a free-form generic OpenAI-compatible endpoint and is appended
after the five named providers. Leave it blank to skip it. Nothing in this list is required —
with only Ollama configured the app behaves exactly as before.

### 5.2 Fallback variables

| Variable                        | Default | Purpose |
|---------------------------------|---------|---------|
| `AI_FALLBACK_ENABLED`           | `true`  | Set to `false` to pin generation to the primary provider and disable failover |
| `AI_PROVIDER_ATTEMPT_TIMEOUT`   | `20`    | Maximum seconds for a **single** provider attempt |
| `AI_PROVIDER_BREAKER_COOLDOWN`  | `60`    | Seconds a failed provider is temporarily skipped before it is retried |

`AI_REQUEST_TIMEOUT` (default `180`) is unchanged and remains the **total** request budget
across all attempts. `AI_PROVIDER_ATTEMPT_TIMEOUT` caps each individual attempt, so a single
unreachable provider cannot consume all 180 seconds before the next one is tried. After a
failure, that provider is fast-failed for `AI_PROVIDER_BREAKER_COOLDOWN` seconds; the
**Test** button on the AI Providers page clears the breaker and runs a real probe.

### 5.3 Preconfigured endpoints and models

Base URLs and model ids ship with vendor-documented defaults and are all overridable in
`.env`:

| Provider     | Default base URL | Default model |
|--------------|------------------|---------------|
| Groq         | `https://api.groq.com/openai/v1` | `openai/gpt-oss-20b` |
| Gemini       | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-3.8-flash` |
| OpenRouter   | `https://openrouter.ai/api/v1` | *(blank — pick from the vendor catalog)* |
| Hugging Face | `https://router.huggingface.co/v1` | *(blank — pick from the vendor catalog)* |

All four are OpenAI-compatible `base_url` values — do **not** include `/chat/completions` in
`.env`; the app appends it. OpenRouter and Hugging Face ship with a blank model on purpose:
their catalogs have hundreds of catalog-dependent ids, so set one explicitly.

### 5.4 API keys

- Keys are **backend-only**. They are read from `backend/.env` on the server and are never
  embedded in frontend code or shipped to the browser.
- The AI Providers page shows only `key_configured` (a boolean) and `key_masked` (a fixed
  `***` redaction). No character of any key is ever returned by the API.
- Leave a provider's `*_API_KEY` blank to mark that slot "Not Configured" — it will be
  skipped by the chain.
- Do not commit a populated `.env`.

Embeddings are unaffected by all of the above and remain Ollama `nomic-embed-text` at
**768 dimensions** (see section 1.1).

## 6. Tests

```bash
cd backend
.venv\Scripts\python -m pytest tests -q
```

Runs against an **isolated temporary database** — never your real data. 60 tests cover
auth, RBAC, project/RAG isolation, uploads, original-file download, task/risk/blocker
workflows, provider status (key-leak check), the fallback chain (ordering, per-attempt
timeouts, circuit breaker, secret masking, endpoint de-duplication, embedding
regression), the agent pipeline and the assistant.
Keep Ollama reachable for full coverage.

Note: the suite must be run as a whole. `tests/test_settings_audit.py` has a pre-existing
order dependency — `test_admin_dashboard` asserts `total_projects >= 1` but relies on
`tests/test_projects_rbac.py` having created projects earlier in the same session. Running
that file alone will fail; that is expected, not a regression.

## 7. Troubleshooting

- **`All AI providers failed`** on analysis → *every* provider in the chain failed. Check
  **Settings → AI Providers**: an offline `detail` and `breaker_open: true` identify the
  culprit. Then work down the chain — Ollama not running or `qwen2.5:3b` / `nomic-embed-text`
  not pulled (`ollama pull qwen2.5:3b`, `ollama pull nomic-embed-text`); a cloud provider with
  `configured: false` (blank `*_API_KEY`); a bad `*_MODEL`; a wrong `*_BASE_URL`; or an
  expired/invalid key. Use the per-provider **Test** button — it clears the circuit breaker
  and runs a real probe, so a `false` result there is trustworthy.
- **A cloud provider stays "Not Configured"** → its `*_API_KEY` is blank, or (for
  `EXTERNAL_PROVIDER_1..3`) its base URL duplicates an earlier provider. Restart the backend
  after editing `.env`.
- **A provider times out** → raise `AI_PROVIDER_ATTEMPT_TIMEOUT`, or lower
  `AI_REQUEST_TIMEOUT` if you want the whole request to fail faster.
- **Fallback not happening at all** → `AI_FALLBACK_ENABLED` may be `false`, which pins
  generation to Ollama.
- **409 / 500 on upload** → confirm the file is a real PDF/DOCX/CSV/TXT (magic bytes are
  checked), under `MAX_UPLOAD_MB`.
- **Port 8000 in use** → edit `run.py` host/port or `HOST`/`PORT` override.
- **Frontend build fails on TS** → run `npm run build` (it runs `tsc -b`); fix flagged types.
- **`setup-admin` returns 403** → an account already exists (this is by design; there is no
  re-seeding).