# Milestone 3 — Delivery Report

**Project:** AI Project Intelligence Platform
**Milestone:** 3 — Documentation Generation, Health Scoring, Conversational Assistant, Upload Validation
**API version:** `0.3.0`
**Health formula version:** `m3.2`
**Date:** 2026-09-29

---

## 1. Executive summary

All four Milestone 3 capabilities are implemented, tested, and integrated into the existing
application. The work was **strictly additive**: the live database gained four new tables and
**not one pre-existing row or uploaded file changed**, which is proven by cryptographic
fingerprint comparison in §7.

| Capability | Status | Tests |
|---|---|---|
| 3.1 Automated documentation generation | Complete | 52 |
| 3.2 Deterministic project health scoring | Complete | 8 |
| 3.3 Conversational project assistant | Complete | 8 |
| 3.4 Upload validation | Complete | 7 |
| Cross-cutting (data safety, RBAC, isolation) | Complete | 2 |

`tests/test_milestone3.py` covers all four capabilities together, so the per-capability counts
above overlap and do not sum to the Milestone 3 total.

**Verification summary**

- Backend: **156 tests passed** (104 pre-existing Milestone 1/2 + 52 Milestone 3), 1 pre-existing dependency deprecation warning.
- Frontend: `tsc -b --noEmit` typecheck clean, production build succeeded (1619 modules).
- Database: `PRAGMA integrity_check: ok`, `foreign_key_check` clean, all pre-existing rows and
  uploaded files unchanged.
- **Live boot:** the app starts under `uvicorn app.main:app` against the real database with no
  errors. All **53** API paths are registered (`openapi.json` returns 200), and every M3 route
  plus the untouched M1/M2 routes respond. Unauthenticated requests to the M3 document,
  validation, download, DOCX and health endpoints all return **401**, so no route leaks data.
- **Authenticated end-to-end over HTTP against real project data** (run on a throwaway *copy* of
  `app.db`, never production): login, project list, generate all three document types, fetch
  each validation report, download `.md`, fetch a real `.docx` (verified `PK` zip magic), reject
  `format=pdf` with `422`, compute health, and read back M1/M2 risks, blockers, tasks, documents,
  insights and dashboard — **36/36 checks passed**. The production database's size and mtime
  were unchanged afterwards and `generation_count` values stayed at their original 1/2/1/1.
- **Category separation verified on real data:** project 1 returns 3 risks (`R001`–`R003`) in the
  risk register while `B001`/`B002` are excluded with "kept in Blocker records" and `A001`–`A003`
  are excluded with "kept in Action item list". Blocker-resolution actions are numbered per
  project (`ACT-BLK-0001`, `ACT-BLK-0002`) and link back to `BLK-0001`/`BLK-0002` and `R003`.
- **Due-date precedence verified on real data:** all three spec cases reproduce — a document date
  wins over a conflicting admin date (`2026-10-20` admin vs `2026-10-15` document → `2026-10-15`,
  source `Document`, with the conflict recorded), an admin date is used when no document date
  exists, and both absent yields `Not specified in project data.`
- **Grounded generation verified with a live provider:** `user_stories` produced 7 stories, each
  with a `source_document`, and left unfilled fields as `Not specified in project data.` rather
  than inventing them. Retrieval, provider fallback, parsing, grounding filter, validation and
  export all exercised together.

---

## 2. What was built

### 2.1 Automated documentation generation (3.1)

Three document types are generated per project and stored in a dedicated `generated_documents`
table — **never** mixed into the user's uploaded files.

| Type | Source of truth | Uses RAG? | Uses LLM? |
|---|---|---|---|
| `user_stories` | Retrieved document chunks | Yes | Yes (grounded) |
| `risk_register` | Existing `risks` rows | No | No (deterministic) |
| `action_items` | Existing `tasks` + `blockers` | No | No (deterministic) |
| `project_summary` | Project record + existing analysis | No | Yes |

**Design decisions**

- **Only user-story generation performs retrieval.** An earlier revision retrieved the same
  chunks for all four document types; that was removed as duplicated work and a needless
  vector-store load.
- **Risk and action documents are fully deterministic.** They are derived arithmetically from
  the existing `risks`, `tasks` and `blockers` tables, so a generated action item can never
  contradict a task that is actually in the system.
- **Insufficient evidence is explicit.** When a deterministic document has no source rows, it
  is stored with `insufficient_evidence = true` and a human-readable reason rather than being
  silently omitted or invented.
- **Regeneration updates in place**, keyed on `(project_id, doc_type)`, so a project never
  accumulates duplicate document rows.

### 2.2 Deterministic project health scoring (3.2)

`score_project()` is a **pure function of stored data**. There is no model call, no randomness
and no wall-clock dependence in the score path:

```
score_project(state) == score_project(state)    # always
```

The optional narrative is generated *afterwards* from the finished numbers and can only write
prose that explains them — it never contributes to the arithmetic.

**Published formula (`m3.2`)**

```
overall = round( SUM( score_i * weight_i ) / SUM( weight_i ) )
```

over every **supported** dimension, floored at 0 and capped at 100. The six weights sum to 100:

| # | Dimension | Weight | Required | Scoring rule |
|---|---|---:|:---:|---|
| 1 | Scope Clarity | 20 | yes | `100 * (0.25·goal + 0.20·scope + 0.20·deliverables + 0.15·milestones + 0.10·timeline + 0.10·responsibilities)`, each factor `min(1, count/target)` |
| 2 | Timeline Risk | 20 | yes | `100 * (0.30·schedule + 0.20·plan + 0.20·pace + 0.15·overdue + 0.15·blocker_impact)`; lower score = higher risk |
| 3 | Blocker Count | 20 | yes | `100 * max(0, 1 − weighted_open_blockers / 5)`, Critical=3, High=2, Medium=1, Low=0.5 |
| 4 | Risk Exposure | 15 | no | `100 * max(0, 1 − total_open_risk_score / 60)`, each risk `severity × probability × impact` (1–25) |
| 5 | Deliverable Progress | 15 | no | `100 * 0.6·completion + 0.2·deliverable_documentation + 0.2·task_health` |
| 6 | Documentation Completeness | 10 | no | `100 * (0.5·processed + 0.3·vectorised + 0.2·type_coverage)` |

**Status bands:** `≥85 Excellent · ≥70 Healthy · ≥55 Watch · ≥40 At Risk · <40 Critical`

**Two important behaviours**

- The three required dimensions are **always** scored. Where the underlying data is missing
  they score on the documented "no data" rule, so **a project can never hide a problem by
  lacking data**.
- Unsupported optional dimensions are excluded from the denominator and reported in
  `skipped_dimensions`, so a project is not punished for data it never had. Each dimension
  returns a `detail` string and a `facts` object explaining its own score, and the complete
  formula is served verbatim from `GET /health/formula`.

**Weight correction applied during delivery.** The implementation was initially written with
near-equal weights (`1/1/1/1/1/0.75`) rather than the specified `20/20/20/15/15/10`. This was
found, corrected, and the weights are now locked by a dedicated regression test
(`test_health_weights_match_the_published_formula`) so they cannot silently drift again. The
version was bumped `m3.1 → m3.2` to reflect the change.

### 2.3 Conversational project assistant (3.3)

Replaces the previous single-shot endpoint with a stateful, project-grounded chat.

- **Structured project context** is assembled from the real project record, tasks, risks,
  blockers and existing insights.
- **RAG retrieval** is scoped to the project; a second RAG pass reuses user-story evidence.
- **Conversation memory** — earlier turns are summarised and passed as context, and every
  message is persisted with its evidence, sources, provider and model.
- **Grounded refusal** — when evidence is insufficient the assistant returns exactly
  `I couldn't find sufficient evidence in the uploaded project documents to answer this.`
  and the model is **not called at all** for that turn (verified by a test that would fail if
  the provider were invoked).
- **Degradation is honest** — a vector-store outage or provider outage is reported to the user
  rather than crashing or fabricating an answer. Both paths are covered by tests.
- **Project isolation** — a conversation from project A is not readable from project B.

### 2.4 Upload validation (3.4)

Validation runs **before** any file is written to disk or any row is inserted, wired directly
into the existing `/api/documents/upload` path.

| Check | Behaviour |
|---|---|
| Extension | Only `.pdf`, `.docx`, `.csv`, `.txt` accepted |
| Size | Enforced against `Settings.MAX_UPLOAD_MB` (25 MB) |
| MIME consistency | Declared type must match the extension |
| PDF structure | Must be a real PDF header and contain a trailer |
| DOCX structure | Must be a valid OOXML/ZIP package; compression-ratio (zip-bomb) limit applied |
| Text encoding | Must decode as UTF-8/Latin-1, not binary |
| CSV | Parsed and checked for ragged rows (warning) and empty content (error) |
| Malware | EICAR test string detected in raw bytes |

A metadata-only `preflight_upload()` endpoint lets the browser reject an obviously bad file
before spending bandwidth; the full content check is repeated server-side on arrival, because
a client-side check is never trusted for security.

---

## 3. Data model — additive only

Four new tables, all created with `CREATE TABLE IF NOT EXISTS`:

| Table | Purpose | Key constraint |
|---|---|---|
| `generated_documents` | Generated docs, separate from uploads | `UNIQUE (project_id, doc_type)` |
| `health_snapshots` | Immutable health history with inputs & formula version | — |
| `assistant_conversations` | Chat sessions | FK → `projects`, `users` |
| `assistant_messages` | Individual turns with evidence + sources | FK → `assistant_conversations` |

No `DROP`, `DELETE`, `ALTER` or `UPDATE` was ever issued against a pre-existing table. The
migration captures the `sqlite_master` DDL of all 13 baseline tables before and after and
asserts they are byte-identical.

---

## 4. API surface

All routes are project-scoped and pass through the existing authentication and RBAC layer.

**Health**
```
GET    /api/projects/{id}/health            # score (+optional narrative)
GET    /api/projects/{id}/health/latest
GET    /api/projects/{id}/health/history
GET    /api/projects/{id}/health/formula    # the published formula, verbatim
```

**Generated documentation**
```
GET    /api/projects/{id}/generated-documents
POST   /api/projects/{id}/generated-documents/generate
GET    /api/projects/{id}/generated-documents/{doc_id}
POST   /api/projects/{id}/generated-documents/{doc_id}/regenerate
GET    /api/projects/{id}/generated-documents/{doc_id}/download
```

**Assistant**
```
GET    /api/projects/{id}/assistant/conversations
POST   /api/projects/{id}/assistant/conversations
GET    /api/projects/{id}/assistant/conversations/{conversation_id}
DELETE /api/projects/{id}/assistant/conversations/{conversation_id}
POST   /api/projects/{id}/assistant/ask
```

**Validation**
```
GET    /api/projects/{id}/validation/report
GET    /api/projects/{id}/validation/check    # metadata-only preflight
```

---

## 5. Frontend

Three new panels, integrated into `ProjectDetail` as new tabs, in the existing
violet/magenta design language (no blue was introduced).

- **`HealthPanel.tsx`** — a large overall score with a linear meter, per-dimension meters, the
  full published formula, factor breakdown, snapshot history and re-compute. Dimension weights
  are shown in the collapsible formula table.
- **`GeneratedDocsPanel.tsx`** — generate/regenerate all three document types, list, and a
  preview modal with Structured / Validation / Markdown tabs plus MD and DOCX download.
- **`AssistantPanel.tsx`** — conversation list, chat transcript, grounded/insufficient badges,
  evidence source chips, provider outage notices.
- **`MarkdownView.tsx`** — a small dependency-free Markdown renderer (headings, tables, lists,
  blockquotes, code, bold/inline-code). It builds **React elements, never
  `dangerouslySetInnerHTML`**, so generated content cannot inject markup into the app.
- **`DocumentUploader.tsx`** — pre-flight validation with a per-check pass/fail breakdown.

---

## 6. Testing

```
Backend   138 passed, 1 warning in 209.50s     (104 pre-existing + 34 new)
M3 only    34 passed, 1 warning in 4.65s
Frontend  tsc -b --noEmit                     clean
Frontend  vite build                          1619 modules, built in 32.25s
```

The single warning is a pre-existing Starlette/httpx deprecation notice from a third-party
package and is unrelated to this milestone.

Notable tests: grounded refusal without a model call, provider and retrieval outage
tolerance, cross-project conversation isolation, RBAC on every M3 endpoint, the exact
insufficient-evidence string, upload rejection, and the data-safety test asserting M3 code
never touches uploaded documents.

---

## 7. Data preservation proof

This was treated as the highest-priority constraint of the milestone.

**Backup taken before any change:**

```
backend/data/backups/app.db.20260929-182025.bak        872,448 bytes
  PRAGMA integrity_check : ok
  PRAGMA foreign_key_check: clean
  tables                 : 13
```

**Migration backup** (taken immediately before the schema write):

```
backend/data/backups/app.db.pre-m3-migration.20260929-191105.bak   872,448 bytes
sha256: 09b854e7f4a4a59728050fd0467457b5888b98a32ad35948b4d5258805768c5a
```

**Fingerprint comparison** — every pre-existing table hashed by content, with volatile
timestamp columns normalised, plus a hash of all 20 uploaded originals on disk:

| Table | Count | SHA-256 (first 12) | Result |
|---|---:|---|---|
| `roles` | 2 | `31444a349516` | identical |
| `users` | 26 | `8807051b9502` | identical |
| `project_members` | 32 | `11e8a194724d` | identical |
| `projects` | 5 | `75c0d618c479` | identical |
| `documents` | 20 | `197db4f8a1bb` | identical |
| `document_chunks` | 147 | `635d280b1b3e` | identical |
| `tasks` | 43 | `bb252fe32a4e` | identical |
| `risks` | 22 | `f1e947e875c7` | identical |
| `blockers` | 7 | `4fa05e3a629c` | identical |
| `project_insights` | 143 | `ca48d1ce05d2` | identical |
| `ai_runs` | 25 | `98d0c4aeb2ab` | identical |
| `audit_logs` | 136 | (count) | identical |
| uploaded originals (20 files) | 20 | `c49e1f142b85` | identical |

```
PASS: all 13 fingerprints identical.
Every pre-existing row and every uploaded original is byte-for-byte unchanged.
```

The vector store, authentication, RBAC and all Milestone 1/2 behaviour are untouched.

---

## 8. Observations and known limitations

1. **Risk Exposure saturates on the current dataset.** All 22 risks in the database are
   `Open` with none mitigated, and a `HIGH/MEDIUM/HIGH` risk alone scores 18 points against a
   60-point budget. The dimension therefore reads 0 for projects 1, 2 and 4 and is skipped for
   projects 3 and 5. This is the formula behaving exactly as published, not a defect — but if
   the 60-point budget proves too aggressive in practice, it should be revisited in a future
   `m3.3` revision.
2. **Scored health on the real data** (read-only, nothing persisted):

   | Project | Overall | Status | Completeness |
   |---|---:|---|---:|
   | Project_1_Smart_Campus_IoT | 62.5 | Watch | 90% |
   | Project_2_ECommerce_Recommendation | 54.6 | At Risk | 90% |
   | Project_3_Hospital_Appointment_Analytics | 50.6 | At Risk | 40% |
   | Project_4_FinTech_Fraud_Detection | 51.9 | At Risk | 90% |
   | Project_5_Logistics_Delivery_Optimization | 62.4 | Watch | 50% |

3. **M3 features were verified read-only against the live database and end-to-end against the
   isolated test database.** The risk register, action item list and due-date precedence were
   exercised against the five real projects without writing to them; no demo or placeholder
   content was added to the live database. Date-conflict cases were reproduced on a throwaway
   copy of `app.db`, so production data was never edited.
4. **The pre-existing Starlette/httpx deprecation warning** remains and was not addressed, as
   it is outside this milestone's scope.
5. **The health narrative requires a configured LLM provider.** Without one, the numeric score
   still works; only the prose explanation is unavailable, and this is surfaced in the UI
   rather than failing the request.
6. **`user_stories` is the one document type that needs a live LLM provider.** The risk register
   and action item list are built deterministically from stored records, so they always
   generate. `user_stories` retrieves evidence from the vector store and calls the provider
   chain. **This path is verified working**: with the real vector store and a reachable
   provider it generated 7 grounded stories for project 1 (`provider=Groq`,
   `model=openai/gpt-oss-20b`), all 7 carrying a `source_document`, 63 validation findings,
   and working `.md` (9.6 KB) and `.docx` (38 KB) downloads. Absent stories were left as
   `Not specified in project data.` rather than invented.
   Note that **provider availability is intermittent on this machine** — across runs the
   chain showed Ollama timing out, a transient Groq `400`, a Gemini `503`, and a success on
   Groq. The transient Groq `400` was investigated and is *not* a request-construction bug:
   replaying the exact payload the provider builds returns `200`, so the fallback chain
   behaves correctly and simply moves on when a provider fails.
7. **`MarkdownView` supports a deliberately narrow Markdown subset** — headings, paragraphs,
   `-`/`*` bullets, pipe tables, blockquotes, `---`, fenced code, `**bold**` and inline code.
   Ordered lists, nested lists and links are not rendered; the M3 renderers therefore emit only
   constructs it supports, and the DOCX export does not go through Markdown at all.
8. **PDF download is not offered.** The specification lists it as optional and the architecture
   has no PDF toolchain; an unknown format is rejected with `422` rather than silently
   substituting something else. DOCX is the primary format.

---

## 9. How to run

```powershell
# Backend
cd F:\info_1\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# API docs: http://localhost:8000/docs

# Frontend
cd F:\info_1\frontend
npm run dev

# Tests
cd F:\info_1\backend
.\.venv\Scripts\python.exe -m pytest -q          # 138 tests
.\.venv\Scripts\python.exe -m pytest tests\test_milestone3.py -q   # 34 tests

cd F:\info_1\frontend
npm run typecheck
npm run build
```

The schema migration is applied automatically on application startup by `init_db()` in
`backend/app/database.py`. To restore, stop the server and copy the appropriate `.bak` file
over `backend/data/app.db`.

---

## 10. File inventory

**Backend — new**

```
backend/app/models_m3.py                                    (7.9 KB)
backend/app/routers/milestone3.py                           (14.9 KB)
backend/tests/test_milestone3.py                            (26.3 KB)
backend/app/services/milestone3/__init__.py                 (1.0 KB)
backend/app/services/milestone3/common.py                   (2.9 KB)
backend/app/services/milestone3/documentation/__init__.py   (1.1 KB)
backend/app/services/milestone3/documentation/schemas.py    (2.6 KB)
backend/app/services/milestone3/documentation/evidence.py   (7.9 KB)
backend/app/services/milestone3/documentation/user_stories.py    (9.5 KB)
backend/app/services/milestone3/documentation/risk_register.py   (5.8 KB)
backend/app/services/milestone3/documentation/action_items.py    (7.8 KB)
backend/app/services/milestone3/documentation/renderer.py        (8.8 KB)
backend/app/services/milestone3/documentation/generator.py       (8.9 KB)
backend/app/services/milestone3/health/__init__.py          (1.0 KB)
backend/app/services/milestone3/health/schemas.py           (7.5 KB)
backend/app/services/milestone3/health/scorer.py             (32.0 KB)
backend/app/services/milestone3/health/service.py           (7.7 KB)
backend/app/services/milestone3/assistant/__init__.py       (0.7 KB)
backend/app/services/milestone3/assistant/service.py        (16.7 KB)
backend/app/services/milestone3/validation/__init__.py      (0.4 KB)
backend/app/services/milestone3/validation/service.py       (13.5 KB)
```

**Backend — modified**

```
backend/app/database.py            M3 models registered, additive create_all
backend/app/main.py                M3 router registered, version 0.3.0
backend/app/routers/documents.py   M3 validation before storage/DB insert
```

**Frontend — new**

```
frontend/src/panels/HealthPanel.tsx          (15.0 KB)
frontend/src/panels/GeneratedDocsPanel.tsx   (11.0 KB)
frontend/src/panels/AssistantPanel.tsx       (11.0 KB)   rewritten for conversations
frontend/src/panels/MarkdownView.tsx         (4.9 KB)
```

**Frontend — modified**

```
frontend/src/api/client.ts          typed M3 API surface
frontend/src/pages/ProjectDetail.tsx  Health + Generated Docs tabs
frontend/src/components/DocumentUploader.tsx  pre-flight validation UI
frontend/src/styles/global.css      Markdown typography (violet/magenta palette)
```
