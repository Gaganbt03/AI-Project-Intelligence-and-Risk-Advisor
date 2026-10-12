# AI Project Intelligence & Risk Advisor

An AI-powered project intelligence platform that transforms project documents into actionable insights. Upload project files, analyze scope and deliverables, identify risks and blockers, generate action items, assess project health, and ask questions about your project using an AI assistant.

The application uses **Retrieval-Augmented Generation (RAG)**, document processing, AI-powered analysis, and session-isolated temporary workspaces to help users understand project information more efficiently.

## Overview

Managing a project often involves reviewing multiple documents, identifying requirements, tracking risks, understanding blockers, and coordinating tasks. Extracting this information manually can be time-consuming and error-prone.

**AI Project Intelligence & Risk Advisor** simplifies this process by allowing users to upload project documents and use AI to extract, analyze, organize, and understand the information contained in them.

The application is designed as a temporary, multi-user workspace. Users can work without creating a project manually, analyze their documents, explore the results, and clear their workspace when finished.

## Key Features

### 1. Document Upload and Processing

Upload project documents directly through the web interface.

Supported file formats:

- PDF (`.pdf`)
- Microsoft Word (`.docx`)
- CSV (`.csv`)
- Plain text (`.txt`)

The document-processing pipeline extracts text, normalizes content, divides it into chunks, and prepares the content for retrieval and AI analysis.

Features include:
- File selection and drag-and-drop upload.
- File validation and upload-size limits.
- Document processing and analysis status.
- Document listing and management.
- Retrieval of relevant content from uploaded documents.

### 2. AI-Powered Project Intelligence

The platform organizes extracted project information into useful analytical views.

**Scope and Deliverables**
- Extract project scope information from source documents.
- Identify requirements, deliverables, and relevant project details.
- Organize extracted information for easier review.

**Risk Analysis and Delivery Forecasting**
- Identify potential project risks supported by available source information.
- Organize risks for review and prioritization.
- Support project delivery and forecast analysis where sufficient evidence exists.

**Blocker and Action-Item Detection**
- Identify documented project blockers.
- Extract relevant action items.
- Help users understand issues requiring attention.

The system is designed to ground its analysis in the uploaded project information rather than treating unsupported assumptions as confirmed facts.

### 3. Project Health Dashboard

Review project health information and analysis results in one place.

The dashboard brings together relevant project insights, risks, blockers, and delivery information to help users understand the current state of their project.

Health indicators and forecasts depend on the information available in the uploaded documents and the analysis performed.

### 4. Generated Documents

Access generated project documents and reports produced by the analysis workflow.

These outputs help organize extracted project information into structured, reviewable artifacts rather than requiring users to manually compile every finding.

### 5. Task Management

Review project-related tasks and action items identified or managed through the application.

Tasks help organize follow-up work associated with project requirements, risks, and blockers.

### 6. Risk Management

Review identified risks and their supporting information.

The risk workflow helps users understand potential issues and evaluate which items may require further investigation or action.

### 7. Blocker Management

Review blockers and associated action items to identify issues that may affect project progress.

The application brings blocker-related information into a dedicated view for easier monitoring and follow-up.

### 8. AI Project Assistant

Ask questions about your uploaded project documents and analysis.

The assistant uses a Retrieval-Augmented Generation (RAG) workflow to retrieve relevant document content and provide context-aware answers.

Example questions:
- What are the main deliverables mentioned in the documents?
- Which risks are identified in the project?
- What blockers are documented?
- What action items require attention?
- Which requirements support a particular finding?

Answers depend on the available source documents and retrieved evidence.

### 9. Anonymous Temporary Workspaces

The application supports multiple users without requiring a traditional login workflow.

Each user receives an isolated workspace associated with a server-managed anonymous session.

Key capabilities:
- No manual project creation or project selection.
- Server-managed session identification using an HttpOnly cookie.
- Separate workspace data for concurrent users.
- Backend enforcement of workspace ownership.
- Session-specific document and analysis access.
- Protection against cross-workspace document access.

Each session is intended to function as a private, temporary project-analysis workspace.

### 10. Session Cleanup and Data Lifecycle

The application includes a session lifecycle designed to remove temporary project data when users finish their work.

**Finish & Clear Session**

The explicit cleanup workflow is designed to remove session-owned data, including:
- Uploaded documents.
- Generated files and reports.
- Project and analysis records.
- Document chunks and associated vector-store data.
- Tasks, risks, blockers, and insights.
- Assistant conversations and related history.
- Other temporary workspace records and files.

**Automatic cleanup**

The lifecycle also includes:
- Browser-close cleanup using a best-effort `pagehide` signal.
- A grace period intended to protect against accidental deletion during a quick page refresh.
- Server-side idle expiration after 60 minutes.
- Cleanup of abandoned sessions through a server-side sweeper.

Browser-close events are not guaranteed to reach the server. Therefore, the explicit cleanup action and server-side expiration mechanism are important parts of the data lifecycle.

## How It Works

The application follows a document-to-intelligence workflow.

1. **Start a workspace:** A user opens the application and receives an anonymous session.
2. **Upload documents:** The user uploads project files directly from the Documents screen.
3. **Extract content:** The backend extracts and normalizes text from supported file formats.
4. **Prepare document chunks:** Extracted content is divided into manageable chunks for retrieval and analysis.
5. **Generate embeddings:** Document chunks are represented as embeddings for semantic retrieval.
6. **Store searchable content:** The vector-store layer makes relevant document content available for retrieval.
7. **Run AI analysis:** The analysis workflow processes project information and produces structured insights.
8. **Review results:** The user explores project health, generated documents, tasks, risks, and blockers.
9. **Ask questions:** The AI assistant retrieves relevant source content to answer project-related questions.
10. **Clear the workspace:** The user finishes the session, triggering cleanup of its temporary data.

## Technical Architecture

The project uses a frontend/backend architecture.

| Layer | Technology / Responsibility |
|---|---|
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI |
| API communication | HTTP APIs with cookie-based session handling |
| Database | SQLite |
| Document processing | PDF, DOCX, CSV, and TXT extraction |
| RAG pipeline | Chunking, embeddings, retrieval, and grounded generation |
| Vector storage | Chroma integration |
| AI analysis | Specialized analysis services and AI provider integrations |
| Testing | Pytest, frontend type checking, and production builds |

### Frontend

The frontend provides the interactive dashboard, document upload experience, analysis views, task and risk interfaces, blocker management, and AI assistant.

The main dashboard navigation is organized as:

**Documents → Health → Generated Docs → Tasks → Risks → Blockers → Assistant**

The application opens directly to the document upload workflow.

### Backend

The FastAPI backend manages API requests, session creation, document processing, workspace ownership, analysis workflows, retrieval, and data cleanup.

The backend is responsible for enforcing session isolation rather than relying only on frontend visibility or browser state.

### Database and Vector Storage

SQLite stores application records and structured project information.

The vector-store integration supports retrieval of relevant document chunks for AI-assisted analysis and question answering.

Session cleanup must account for both structured database records and associated files and vector-store data.

## Retrieval-Augmented Generation (RAG)

RAG combines document retrieval with language-model generation.

The general workflow is:

**Uploaded Documents → Text Extraction → Chunking → Embeddings → Vector Retrieval → Context Assembly → AI Response**

When a user asks a question:

1. The system processes the question.
2. Relevant document content is retrieved.
3. Retrieved context is provided to the AI generation workflow.
4. The assistant generates an answer using the available evidence.

RAG helps connect AI responses to project-specific information instead of relying exclusively on general model knowledge.

The quality of the answer depends on document quality, retrieval relevance, available context, and the selected AI provider.

## AI Analysis Architecture

The backend organizes project intelligence into specialized analysis components and services.

These cover areas such as:
- Scope and deliverable extraction.
- Risk identification and analysis.
- Delivery forecasting.
- Blocker detection.
- Action-item extraction.
- Analysis orchestration.
- Document retrieval and grounded assistant responses.

AI outputs should be reviewed before being used for important project decisions. The platform assists project analysis; it does not replace professional judgment or guarantee that every finding is complete.

## Security and Privacy Design

The application is designed around session-isolated temporary workspaces.

Important security principles include:

- **Server-side ownership enforcement:** API requests must be checked against the current session's workspace.
- **Session isolation:** Users must not access or modify another session's records.
- **Cookie-based sessions:** Session cookies should use appropriate security attributes, including HttpOnly and suitable Secure and SameSite settings for the deployment environment.
- **Scoped deletion:** Cleanup should remove only the intended session's data.
- **No frontend-only authorization:** Hiding a resource in the UI is not a substitute for backend authorization.
- **Secret management:** AI API keys and other secrets belong in environment configuration, not source code.
- **Data minimization:** Temporary documents and generated content should not be retained longer than the intended session lifecycle.

For public deployment, HTTPS, secure cookie configuration, request-origin/CSRF protections where applicable, rate limiting, and production-grade operational monitoring should be configured and verified.

## Getting Started

### Prerequisites

Install the following tools:

- Python compatible with the backend's declared requirements.
- Node.js and npm compatible with the frontend project.
- Git.
- Any AI provider credentials required by your chosen configuration.

### 1. Clone the repository

```bash
git clone https://github.com/Gaganbt03/AI-Project-Intelligence-and-Risk-Advisor.git
cd AI-Project-Intelligence-and-Risk-Advisor
```

### 2. Set up the backend

Open a terminal in the repository's `backend` directory.

On Windows PowerShell:

```powershell
cd backend

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
```

Configure the backend environment using the provided `.env.example` file and the variables documented by the project. Add any required AI provider credentials locally.

Do not commit `.env` files containing secrets.

Start the backend:

```powershell
python run.py
```

The development backend is configured to run at:

```text
http://127.0.0.1:8000
```

### 3. Set up the frontend

Open another terminal in the repository's `frontend` directory.

```powershell
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite in your terminal. The development frontend is typically available at:

```text
http://localhost:5173
```

Keep the backend and frontend running while using the development application.

### 4. Verify the frontend

Run the TypeScript checks:

```powershell
npm run typecheck
```

Create a production build:

```powershell
npm run build
```

Use the exact Python interpreter and dependency versions specified by your environment when running backend tests.

## Testing

The project includes backend tests for document processing, session lifecycle, workspace isolation, API behavior, AI analysis, and related workflows.

From the `backend` directory, activate the virtual environment and run:

```powershell
python -m pytest
```

Run a focused session-lifecycle test group:

```powershell
python -m pytest tests/test_workspace_lifecycle.py -q
```

Run focused document tests:

```powershell
python -m pytest tests/test_documents.py -q
```

Run the frontend checks from `frontend`:

```powershell
npm run typecheck
npm run build
```

The full backend suite may take longer than individual test groups. Reported targeted test results should not be interpreted as proof that every test passes in a single full-suite run.

Important scenarios to verify before deployment include:
- Upload and retrieval of supported document formats.
- Correct document ownership.
- Isolation between simultaneous sessions.
- Explicit session cleanup.
- Protection of other sessions' data during cleanup.
- Idle expiration and abandoned-session handling.
- Refresh behavior during the browser-close grace period.
- Failure recovery during file or vector-store cleanup.
- Frontend and backend integration.

## Repository Structure

The repository is organized into separate frontend and backend applications.

```text
AI-Project-Intelligence-and-Risk-Advisor/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   ├── models/
│   │   ├── routers/
│   │   ├── services/
│   │   ├── ...
│   │   └── main.py
│   ├── tests/
│   ├── run.py
│   ├── requirements.txt
│   └── .env.example
│
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── layout/
│   │   ├── pages/
│   │   ├── workspace/
│   │   └── styles/
│   ├── package.json
│   └── vite.config.ts
│
└── README.md
```

This is a high-level guide to the repository organization. Refer to the actual source tree for the exact names of all modules and files.

## API Overview

The backend exposes API endpoints for session management, document operations, project-related workspace data, analysis, and assistant functionality.

The session lifecycle includes endpoints such as:

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/session/start` | Start an anonymous workspace session |
| `GET` | `/api/session/current` | Retrieve the current session's workspace information |
| `POST` | `/api/session/heartbeat` | Refresh activity for the current session |
| `POST` | `/api/session/finish` | Finish and clear the current workspace |

Other API routes support document operations, dashboard data, tasks, risks, blockers, generated documents, and assistant interactions.

The session endpoints require the appropriate session cookie. Refer to the backend router implementations and API documentation for exact request schemas and response formats.

## Configuration

Configuration is managed through the backend environment and the frontend's development setup.

Before running the application:
1. Review `backend/.env.example`.
2. Configure the required AI provider credentials and model settings.
3. Confirm the database and upload-storage locations.
4. Confirm the frontend API base URL.
5. Never publish API keys, private credentials, or production session secrets.

The supported providers, model names, fallback behavior, and environment variable names should be taken from the current configuration and provider implementation rather than assumed from this README.

## Current Scope and Limitations

- Analysis quality depends on the completeness and clarity of the uploaded documents.
- Unsupported or undocumented project details may remain unspecified.
- AI-generated outputs require review before important decisions are made.
- Browser-close signals are best-effort; server-side expiration provides the fallback.
- Session cleanup must be monitored for partial failures in database, file-storage, and vector-store operations.
- A successful frontend build does not by itself establish backend security or production readiness.
- A targeted test pass is not equivalent to a clean full-suite pass.
- Production deployment requires secure cookie settings, HTTPS, appropriate origin protections, monitoring, and verification of every user-management and data-access endpoint.

## Future Enhancements

Potential areas for further development include:
- Richer project-health visualizations.
- More detailed delivery forecasting.
- Improved risk prioritization and traceability to source documents.
- Additional document formats.
- Expanded analysis evaluation and regression testing.
- Better operational monitoring for cleanup and session expiration.
- Production deployment automation and security testing.

## Intended Use

This project demonstrates how document processing, RAG, AI-powered analysis, and session-based application design can be combined to support project intelligence and risk analysis.

It can be used as a foundation for exploring AI-assisted project management, evidence-based risk identification, document intelligence, and temporary multi-user workspaces.

## Author

**Gagan BT**

- GitHub: https://github.com/Gaganbt03
- Project repository: https://github.com/Gaganbt03/AI-Project-Intelligence-and-Risk-Advisor

---

*AI Project Intelligence & Risk Advisor — Turn project documents into actionable intelligence.*
