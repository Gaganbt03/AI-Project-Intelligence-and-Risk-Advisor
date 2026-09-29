# Architecture — AI Project Intelligence & Risk Advisor

## System overview

```
 ┌─────────────────────────── SCREEN (SPA) ───────────────────────────┐
 │ frontend/  React 18 + Vite + TypeScript                            │
 │  - AuthContext (JWT in localStorage), role-aware sidebar            │
 │  - Pages: Projects / ProjectDetail / Documents / Insights / Tasks   │
 │           Risks / Blockers / Employees / AiSettings / AuditLogs /   │
 │           Profile / ReportBlocker / Assistant / auth pages          │
 │  - Panels: DocsPanel, TasksPanel, RisksPanel, BlockersPanel,        │
 │            InsightsPanel, AssistantPanel (reused across pages)      │
 │  - Toast system, custom SVG charts, agent-step progress UI          │
 │  - `api/client.ts` = single typed API wrapper (XHR uploads, auth)   │
 └──────────────────────────────┬──────────────────────────────────────┘
                                │ REST (JSON)  →  vite proxy /api → :8000
 ┌──────────────────────────────▼──────────────────────────────────────┐
 │ backend/  FastAPI application (app.main:app)                        │
 │                                                                     │
 │ Routers (app/routers/)                     Services (app/services/) │
 │  auth, users, projects,                    security   (JWT+bcrpyt)  │
 │  documents, risks, blockers,               audit      (event log)   │
 │  tasks, insights, assistant,                bootstrap  (roles+admin) │
 │  dashboard, settings, audit                documents  (pipeline)    │
 │                                             extract    (pdf/docx/   │
 │  deps.py  → Bearer auth, ADMIN guard,                 csv/txt)      │
 │             project/doc access check        chunking                │
 │             (project isolation)             embeddings (ollama)     │
 │                                            rag        (retrieval)   │
 │                                            vector_store (chromadb)  │
 │                                            assistant  (grounded QA) │
 │                                            projects   (out shapes)  │
 │                                            ai/providers (failover)  │
 │                                            agents/                  │
 │                                              orchestrator +         │
 │                                              scope/risk/forecast/   │
 │                                              blocker/action agents  │
 └───────────────┬───────────────────────────────────────────┬─────────┘
                 │ SQLAlchemy                                │ Chroma + files
        ┌────────▼─────────┐                     ┌───────────▼───────────┐
        │ SQLite (data/)   │                     │ data/uploads (originals)
        │ roles/users/     │                     │ data/vector_db (collections)
        │ projects/tasks/  │                     │ embeddings: ollama nomic-embed
        │ risks/blockers/  │                     └───────────┬───────────┘
        │ docs/insights/   │                                 │
        │ audit_logs/      │                     ┌───────────▼───────────┐
        └──────────────────┘                     │ Ollama (localhost)    │
                                                 │ qwen2.5:3b generation │
                                                 │ nomic-embed-text emb   │
                                                 └───────────────────────┘
```

## Key flows

### 1. Authentication & authorization
- `AuthContext` calls `/api/auth/status` → shows the **Create administrator** screen when the
  DB has zero users, otherwise login.
- JWT (HS256, `SECRET_KEY`) carries `sub`+`role`. `deps.get_current_user` resolves the user on
  every request; `require_admin` gates admin routes.
- Project membership is enforced by `get_accessible_project`: admins pass through; employees
  must have a `ProjectMember` row. Documents pass the same check through their parent project,
  so **RAG and file access are isolated per project**.

### 2. Document ingestion
1. `POST /api/documents/upload?project_id=` — magic-byte validation (real PDF/DOCX/CSV/TXT),
   original binary stored to `data/uploads/<project>/` (never modified).
2. Record created with status `Uploaded`; a background task runs `process_document`:
   extract → chunk (`CHUNK_SIZE`/`CHUNK_OVERLAP`) → embed batch (ollama `nomic-embed-text`)
   → store vectors in the project-scoped Chroma collection → status `Processed`.
3. `download` returns the stored binary; `preview` returns extracted text; `reprocess`
   recomputes chunks/vectors (admin only).

### 3. Multi-agent analysis (`analyze_project`)
`POST /api/projects/{id}/insights` (admin) runs agents in order
`scope → risk → forecast → blocker → action`:

- Each agent retrieves the project's top chunks (RAG, `AI_MAX_CHUNKS`), builds a task prompt,
  and the provider layer returns JSON that is **validated against a Pydantic schema**
  (`app/services/ai/schemas.py`). Invalid output is recorded, not crashed.
- Results are persisted (`ProjectInsight`, plus `Risk`/`Blocker`/`Task` rows for the relevant
  agents) with `evidence` = source citations (document, page, section, quote).
- Every run is recorded in `AiRun` (agent, provider, model, status, error, sources) and in the
  audit log. Prior agent outputs are cleared so runs don't duplicate.
- `health` metrics are computed from real data (task completion, schedule status from the
  forecast agent, documents, open risks/blockers) for the dashboard and future scoring.

### 4. Assistant (grounded Q&A)
`POST /api/projects/{id}/assistant` embeds the question, retrieves the project's chunks via
Chroma, builds a grounded prompt and returns `{ answer, sources, grounded, provider, model }`.
Retrieval is **always** restricted to that project's collection — cross-project leakage is
impossible by construction.

### 5. AI provider failover
`ProviderManager` orders providers: **Ollama (primary)** → External 1 → 2 → 3 (OpenAI-compatible
`/chat/completions`). Configured external providers participate automatically; failures fall
through the chain. The status endpoint exposes health/names/models but **never** API keys.

## Data model (SQLAlchemy, `app/models.py`)

- `Role`, `User`, `ProjectMember` — RBAC + membership
- `Project`, `ProjectDocument` (+chunks/vectors in Chroma), `Task`, `Risk`, `Blocker`
- `ProjectInsight`, `AiRun` — AI output and run history
- `AuditLog` — security/activity trail
- `SystemSetting` — runtime-overridable chunking/temperature values

## Frontend structure

```
frontend/src/
  api/client.ts          single API client (auth header, XHR upload, downloads)
  auth/AuthContext.tsx   session state, setup/login/logout, role
  ui/ToastContext.tsx    global toasts
  layout/AppShell.tsx    sidebar (role-aware nav) + topbar + content scroll
  components/            Modal, Badge, EmptyState, ProjectSelector, DocumentUploader
  panels/                reusable project-scoped panels used by pages and tabs
  pages/                 route-level screens (see overview diagram)
  styles/global.css      design system (violet/magenta tokens, no blue dominance)
```

## Security notes

- Passwords bcrypt-hashed; JWT expires; inactive accounts are rejected at login and on token use.
- API keys are server-side only and excluded from every API response.
- Upload type/size validated; files stored outside any web-served directory.
- Original binaries are immutable: extraction/embedding never rewrites the stored file.