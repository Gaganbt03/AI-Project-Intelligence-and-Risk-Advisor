# AI Project Intelligence & Risk Advisor

A full-stack project intelligence platform that turns uploaded project documents into
structured, actionable insight using a local, privacy-first AI stack.

Users upload PDF / DOCX / CSV / TXT documents for a project. The backend extracts text,
chunks it, and embeds everything into a **project-isolated** vector store (RAG). A chain of
AI agents then analyzes the project knowledge base and produces:

- **Scope** — goals, deliverables, milestones, responsibilities, technologies, requirements
- **Risks** — schema-validated risk register with evidence and source citations
- **Forecast** — delivery forecast, schedule status and contributing factors
- **Blockers** — detected issues with the underlying evidence fragment
- **Action items** — recommended tasks with assignees and deadlines, written back to the task board
- **Assistant** — a project-scoped Q&A chat. Answers surface the document chunks they are
  grounded in, so every claim is traceable.

## Highlights

- **No demo data.** The app starts empty. The first real Administrator is created through the
  setup screen (or your environment). Nothing is seeded.
- **Privacy-first AI.** Default runtime is 100% local via **Ollama** — `qwen2.5:3b` for
  generation and `nomic-embed-text` for embeddings. Optional OpenAI-compatible external
  providers can be added as an automatic failover chain; API keys never reach the browser.
- **Original files are preserved.** Downloads return the exact binary that was uploaded —
  never a regenerated copy.
- **Role-based access.** `ADMIN` vs `EMPLOYEE` enforcement happens server-side on every route.
- **Project isolation.** RAG retrieval, documents, insights and finances are strictly scoped
  to the projects a user is a member of.
- **Audit trail.** Sign-ins, uploads, downloads, AI runs and entitlement changes are recorded.
- **Premium UI.** Dark violet/magenta design system, glass panels, agent step progress, empty
  states and a role-aware sidebar.

## Architecture (short)

```
frontend/   React 18 + Vite + TypeScript SPA  (port 5173, proxies /api)
backend/    FastAPI + SQLAlchemy + ChromaDB + Ollama  (port 8000)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full component map and [API.md](API.md) for
the endpoint reference.

## Getting started

Follow [SETUP.md](SETUP.md) for a step-by-step install.

### Prerequisites

- Python 3.11+ (developed on 3.13)
- Node.js 20+
- Ollama running locally with:
  - `ollama pull qwen2.5:3b`
  - `ollama pull nomic-embed-text`

### Quick start

```bash
# Backend
cd backend
python -m venv .venv && .venv\Scripts\activate   # (Windows) / source .venv/bin/activate (mac/Linux)
pip install -r requirements.txt
copy .env.example .env                           # cp .env.example .env on mac/Linux
python run.py                                    # http://127.0.0.1:8000 (docs at /docs)

# Frontend (new terminal)
cd frontend
npm install
npm run dev                                      # http://localhost:5173
```

Open http://localhost:5173, complete the **Create administrator** step (first run only),
then log in and create your first project.

## Testing

```bash
cd backend
.venv\Scripts\python -m pytest tests -q          # 38 test cases, isolated temp DB
```

The suite covers auth, RBAC, project and RAG isolation, document upload/download of the
original binary, task/risk/blocker flows, AI provider status (without leaking keys), the
agent run pipeline and the assistant. Ollama should be reachable for full coverage; the
analysis test is intentionally tolerant of AI provider outages.

## Production notes

- Set a long random `SECRET_KEY`.
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` can bootstrap the first admin from the environment and
  should be removed afterwards.
- Add OpenAI-compatible external providers in `.env` for automatic failover if Ollama is
  unavailable or for higher quality generation.
- The vector store, uploads and SQLite DB all live under `backend/data/` (gitignored).