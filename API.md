# API Reference

Base URL: `http://127.0.0.1:8000` — all endpoints are under `/api`. Interactive docs at
`/docs` (Swagger UI) and `/redoc`.

## Authentication

All endpoints except the three below require a Bearer token:

```
Authorization: Bearer <access_token>
```

### `GET /api/auth/status`
Indicates whether setup is still pending.

```json
{ "needs_setup": true }
```

### `POST /api/auth/setup-admin`
Create the **first** administrator. Only succeeds while the database has zero users.
Body: `{ name, email, password }` (password ≥ 8 chars). Returns `LoginResponse`.

403 if setup is already complete.

### `POST /api/auth/login`
Body: `{ email, password }`.

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": { "id": 1, "email": "admin@x.com", "name": "Admin", "role": "ADMIN", "is_active": true }
}
```

### `POST /api/auth/logout` — auth required
### `GET  /api/auth/me` — auth required
### `POST /api/auth/change-password` — auth required
Body: `{ old_password, new_password }`.

## Projects

| Method | Path                     | Access  | Purpose                       |
|--------|--------------------------|---------|-------------------------------|
| GET    | `/api/projects`          | any     | Projects visible to the user  |
| POST   | `/api/projects`          | ADMIN   | Create project               |
| GET    | `/api/projects/{id}`     | members | Project detail               |
| PUT    | `/api/projects/{id}`     | ADMIN   | Update project               |
| DELETE | `/api/projects/{id}`     | ADMIN   | **Archive** (soft)           |

Create/update body: `{ name, description?, objective?, manager_id?, start_date?,
expected_end_date?, priority?, status?, member_ids? }`. Posts return `ProjectOut` with
`manager_name`, `member_count`, `document_count`, `task_count`, `member_ids`.

## Users (admin only)

| Method | Path                          | Purpose                                    |
|--------|-------------------------------|--------------------------------------------|
| GET    | `/api/users`                  | List users                                 |
| POST   | `/api/users`                  | Create `{ name, email, password, role, project_ids }` |
| PUT    | `/api/users/{id}`             | Update `{ name?, password?, is_active? }` |
| PUT    | `/api/users/{id}/projects`    | Replace memberships `{ project_ids }`      |
| POST   | `/api/users/{id}/reset-password` | Body `{ password }`                     |

## Documents

| Method | Path                                  | Access      | Purpose                             |
|--------|---------------------------------------|-------------|-------------------------------------|
| GET    | `/api/documents`                      | any         | List. Filters: `project_id`, `file_type`, `status`, `uploaded_by` |
| POST   | `/api/documents/upload?project_id=`   | members     | Multipart `file` (PDF/DOCX/CSV/TXT) |
| GET    | `/api/documents/{id}`                 | members     | Document metadata                   |
| GET    | `/api/documents/{id}/preview`         | members     | `{ text, chunk_count, page_count, has_more, ... }` (max 8000 chars) |
| GET    | `/api/documents/{id}/download`        | members     | **Original uploaded binary**        |
| POST   | `/api/documents/{id}/reprocess`       | ADMIN       | Recompute chunks + vectors          |
| DELETE | `/api/documents/{id}`                 | ADMIN       | Delete doc + vectors                |

Document status: `Uploaded → Processing → Processed | Failed`. `embedding_status` is tracked
separately.

## Tasks

| Method | Path                    | Access        | Purpose |
|--------|-------------------------|---------------|---------|
| GET    | `/api/tasks`            | any           | List. Query: `project_id` (**required**), `status`, `assigned_to` |
| POST   | `/api/tasks`            | ADMIN         | Create manual task `{ title, description?, assigned_to?, due_date?, priority?, status?, source_document_id? }` |
| PUT    | `/api/tasks/{id}`       | assignee/ADMIN | Update (admin may reassign)         |

## Risks

| Method | Path              | Access | Purpose |
|--------|-------------------|--------|---------|
| GET    | `/api/risks`       | any    | List. Query: `project_id` (**required**), `severity`, `status` |
| POST   | `/api/risks`       | members| Create manual risk |
| PUT    | `/api/risks/{id}`  | members| Update (severity, probability, impact, status, evidence, recommended_action, title) |

Manual risks carry `source_type: "manual"`; AI output uses `"ai_detected"`.

## Blockers

| Method | Path                 | Access | Purpose |
|--------|----------------------|--------|---------|
| GET    | `/api/blockers`      | any    | List. Query: `project_id` (**required**), `status` |
| POST   | `/api/blockers`      | members| Report a blocker |
| PUT    | `/api/blockers/{id}` | members| Update status etc. |

Reported blockers have `source_type: "employee_reported"`; AI-detected ones `"ai_detected"`
and carry an `evidence` fragment.

## AI Insights / Agent Runs

| Method | Path                                     | Access     | Purpose |
|--------|------------------------------------------|------------|---------|
| GET    | `/api/projects/{id}/insights`            | members    | All insight records (agent, category, title, summary, payload, evidence, created_at) |
| GET    | `/api/projects/{id}/insights/health`     | members    | `{ has_analysis, metrics }`          |
| POST   | `/api/projects/{id}/insights`            | ADMIN      | Run all five agents synchronously    |
| GET    | `/api/projects/{id}/insights/runs`       | members    | Recent `AiRun` history (provider, model, status, error) — the provider/model recorded is the chain slot that answered |

`POST /insights` returns:

```json
{
  "project_id": 1,
  "agents": {
    "scope":    { "status": "Completed", "count": 5,  "error": null },
    "risk":     { "status": "Completed", "count": 3,  "error": null },
    "forecast": { "status": "Completed", "count": 1,  "error": null },
    "blocker":  { "status": "Completed", "count": 2,  "error": null },
    "action":   { "status": "Completed", "count": 4,  "error": null }
  },
  "health_metrics": { "task_completion_rate": 0.25, "schedule_status": "At Risk", "documents": 4, "open_risks": 1, "open_blockers": 0, "total_tasks": 4, "edge": "..." }
}
```

Per-agent failures never 500 — they are recorded in the run with `status: "Failed"`.

## Assistant

### `POST /api/projects/{id}/assistant` — members
Body: `{ question }` (non-blank, ≤ 2000 chars).

```json
{
  "answer": "The delivery is at risk...",
  "sources": [
    { "document": "status.csv", "page": null, "section": "Payments API", "quote": "Behind..." }
  ],
  "provider": "Ollama",
  "model": "qwen2.5:3b",
  "grounded": true,
  "error": null
}
```

Retrieval is strictly scoped to the project's vector collection.

`provider` and `model` report whichever provider in the fallback chain actually answered —
`Ollama` / `qwen2.5:3b` while the primary is healthy, otherwise the fallback that succeeded
(see [AI provider chain](#ai-provider-chain)). When no provider can answer, `provider` and
`model` are empty strings and `error` carries the aggregated reason.

## Dashboards

| Method | Path                 | Access | Purpose |
|--------|----------------------|--------|---------|
| GET    | `/api/admin/dashboard` | ADMIN  | `{ summary, projects[], recent_insights[], recent_activity[] }` |
| GET    | `/api/me/dashboard`    | any    | `{ projects[], my_tasks[], pending_action_items, completed_tasks, open_blockers, open_risks, recent_documents[], recent_insights[] }` |

`summary`: `{ total_projects, active_projects, team_members, documents, open_risks,
critical_risks, open_blockers }`.

## Settings

| Method | Path                                   | Access  | Purpose |
|--------|----------------------------------------|---------|---------|
| GET    | `/api/settings/general`                | any     | Public-safe chunking defaults + `max_upload_mb` |
| GET    | `/api/admin/settings/general`          | ADMIN   | Full general config: chunking values + `ollama_base_url`, `ollama_model`, `embedding_provider`, `embedding_model` |
| PUT    | `/api/admin/settings/general`          | ADMIN   | Update `{ chunk_size?, chunk_overlap?, ai_max_chunks?, ai_temperature? }` |
| GET    | `/api/admin/settings/ai-providers`     | ADMIN   | Ordered provider chain status + embedding health. **Never returns API keys.** |
| POST   | `/api/admin/settings/ai-providers/{key}/test` | ADMIN | Test one provider. `key` = `ollama` \| `groq` \| `gemini` \| `external_1` \| `external_2` \| `external_3` |

### AI provider chain

Generation is served by a **deterministic, sequential** fallback chain. Providers are never
called in parallel — the first one that returns a valid answer wins, and the request moves on
to the next provider when one is unconfigured, unreachable, times out, or returns an error.

| Order | `key`         | Provider       | Role         | Configured by                                 |
|-------|---------------|----------------|--------------|-----------------------------------------------|
| 1     | `ollama`      | Ollama         | `primary`    | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (no key)    |
| 2     | `groq`        | Groq           | `fallback_1` | `GROQ_BASE_URL`, `GROQ_API_KEY`, `GROQ_MODEL` |
| 3     | `gemini`      | Gemini         | `fallback_2` | `GEMINI_BASE_URL`, `GEMINI_API_KEY`, `GEMINI_MODEL` |
| 4     | `external_1`  | OpenRouter     | `fallback_3` | `EXTERNAL_PROVIDER_1_*`                       |
| 5     | `external_2`  | Hugging Face   | `fallback_4` | `EXTERNAL_PROVIDER_2_*`                       |
| 6     | `external_3`  | OpenAI-Compatible | `extra`   | `EXTERNAL_PROVIDER_3_*` — generic escape hatch, appended last |

All rows are always returned, whether or not they are configured, so the UI can render the
whole chain. `in_chain` is `false` for a slot that is not participating (no key, or its base
URL duplicates an earlier provider).

Set `AI_FALLBACK_ENABLED=false` to pin generation to the primary provider and disable
failover entirely.

### Timeouts and failover

| Setting | Default | Meaning |
|---------|---------|---------|
| `AI_REQUEST_TIMEOUT` | `180` | **Total** budget in seconds for one request across all attempts. |
| `AI_PROVIDER_ATTEMPT_TIMEOUT` | `20` | Maximum seconds for a single provider attempt. |
| `AI_PROVIDER_BREAKER_COOLDOWN` | `60` | Seconds a failed provider is skipped (circuit open) before being retried. |

Each attempt is capped at `AI_PROVIDER_ATTEMPT_TIMEOUT` (and at whatever remains of
`AI_REQUEST_TIMEOUT`), so one unreachable provider cannot consume the whole budget before the
next one is tried. After a failure the provider is fast-failed for
`AI_PROVIDER_BREAKER_COOLDOWN` seconds, reported as `breaker_open: true`; the Test endpoint
clears that breaker so the button always performs a real probe.

When every provider fails, generation raises a single aggregated error listing each provider
and its reason.

### `ai-providers` response

```json
{
  "providers": [
    {
      "key": "ollama", "name": "Ollama", "kind": "ollama", "index": 0,
      "role": "primary", "order": 1, "model": "qwen2.5:3b",
      "configured": true, "in_chain": true,
      "key_configured": false, "key_masked": "",
      "healthy": true, "breaker_open": false,
      "detail": "Connected · qwen2.5:3b available", "primary": true
    },
    {
      "key": "groq", "name": "Groq", "kind": "external", "index": 1,
      "role": "fallback_1", "order": 2, "model": "openai/gpt-oss-20b",
      "configured": true, "in_chain": true,
      "key_configured": true, "key_masked": "***",
      "healthy": false, "breaker_open": false,
      "detail": "Error: request failed", "primary": false
    }
  ],
  "embedding": { "provider": "ollama", "model": "nomic-embed-text", "dimension": 768, "healthy": true }
}
```

Field notes:

- `configured` — the slot has a complete configuration (base URL, API key, model). Independent
  of reachability.
- `healthy` / `detail` — result of a live probe of that provider.
- `key_configured` — whether an API key is set.
- `key_masked` — a fixed redaction (`"***"`, or `""` when no key is set). **No character of
  any key is ever returned.** Keys stay backend-only; the browser only ever sees these two
  fields.
- `role` / `order` — position in the chain; `primary` is `true` only for Ollama.
- `embedding` — always the local Ollama embedding provider. Embeddings are never routed to a
  cloud provider and are independent of the LLM chain above.

### `POST /api/admin/settings/ai-providers/{key}/test`

```json
{
  "key": "groq", "name": "Groq", "role": "fallback_1",
  "model": "openai/gpt-oss-20b", "configured": true,
  "healthy": false, "detail": "Error: request failed"
}
```

An unknown `key` returns `400`.

## Audit Logs

`GET /api/admin/audit-logs` — ADMIN. Query: `limit` (≤1000, default 200), `offset`, `action`,
`user_id`. Returns `{ total, items[] }` where items carry `user_email`, `action`,
`resource_type`, `resource_id`, `project_id`, `detail`, `ip_address`, `created_at`.

## Health / meta

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/`  | Service banner (name, docs, frontend URL) |
| GET    | `/api/health` | `{ status: "ok", app, env }` |

## Error format

Errors use the standard FastAPI shape:

```json
{ "detail": "You do not have access to this project." }
```

Status codes: `401` unauthenticated/expired, `403` role or project access denied,
`404` missing resource, `400`/`422` validation or bad payload.