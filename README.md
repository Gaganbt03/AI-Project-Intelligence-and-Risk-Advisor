# AI Project Intelligence & Risk Advisor

An AI-powered project management and intelligence platform that analyzes project documents, extracts actionable information, identifies risks and blockers, forecasts delivery health, and provides grounded project insights using Retrieval-Augmented Generation (RAG) and AI agents.

---

## 📌 Overview

**AI Project Intelligence & Risk Advisor** is a full-stack AI application designed to help project teams understand and manage information from their existing project documents.

Instead of manually reading proposals, requirements, meeting notes, progress reports, and task files, users can upload project documents and let the system automatically analyze the available information.

The platform combines:

- Retrieval-Augmented Generation (RAG)
- AI agents
- Document processing
- Vector search
- Project health analysis
- Risk detection
- Blocker identification
- Task and action-item extraction
- Delivery forecasting
- AI-generated project documentation
- Conversational project assistance

The system is designed to keep AI-generated information grounded in uploaded project data rather than generating unrelated or unsupported information.

## ✨ Key Features

- **Scope:** Goals, deliverables, milestones, responsibilities, technologies, and requirements.
- **Risks:** Schema-validated risk register with supporting evidence and source citations.
- **Forecast:** Delivery forecast, schedule status, and contributing factors.
- **Blockers:** Detected project issues with supporting evidence.
- **Action Items:** Recommended tasks with assignees and deadlines, written back to the task board.
- **Project Health:** Formula-driven score with an explainable breakdown, history, and recommendations derived from project data.
- **Generated Documents:** User stories, risk registers, and action-item lists based on project evidence, exportable as Markdown or DOCX.
- **Assistant:** Project-scoped question-answering chat with references to the document chunks supporting its answers.

### 📄 Intelligent Document Upload

Upload project-related files such as:

- PDF
- DOCX
- TXT
- CSV

The system extracts and processes document content automatically. After upload, the project analysis pipeline processes the available information and updates the project's intelligence.

### 🔎 Retrieval-Augmented Generation (RAG)

The application uses RAG to provide project-specific AI responses.

The general workflow is:

```text
Project Documents
       ↓
Document Extraction
       ↓
Text Chunking
       ↓
Embeddings
       ↓
Vector Database
       ↓
Relevant Context Retrieval
       ↓
AI Processing
       ↓
Grounded Project Information
```

### 🔐 Privacy and AI Providers

- **Local AI:** The documented default runtime uses Ollama, with `qwen2.5:3b` for generation and `nomic-embed-text` for embeddings.
- **External providers:** Optional OpenAI-compatible providers can be configured as an automatic fallback chain.
- **Internal provider usage:** AI providers support generation, RAG-grounded assistant answers, automatic analysis, and embeddings. Provider configuration remains an internal application capability.

### 📁 Document Preservation

The existing application is designed to preserve original uploaded files so that downloads return the original uploaded binary rather than a regenerated copy.

### 🧩 Project Isolation and Access Control

The existing architecture includes server-side role enforcement and project-scoped data access. Document retrieval, analysis results, and RAG context are intended to remain restricted to the appropriate project and authorized users.

### 📝 Audit Trail

The documented application records activities such as sign-ins, uploads, downloads, AI runs, and entitlement changes.

### 🎨 User Interface

The application uses a dark violet and magenta visual design with glass-style panels, agent progress indicators, and contextual navigation.

---

## ⚙️ Technology and Architecture

The platform combines a frontend application, backend services, document-processing pipelines, vector search, and AI-powered analysis agents.

See [ARCHITECTURE.md](ARCHITECTURE.md) for architecture details and [SETUP.md](SETUP.md) for setup instructions.

