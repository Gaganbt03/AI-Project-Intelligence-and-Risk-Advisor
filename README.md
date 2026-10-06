# AI Project Intelligence & Risk Advisor

An AI-powered project management and intelligence platform that analyzes project documents, extracts actionable information, identifies risks and blockers, forecasts delivery health, and provides grounded project insights using RAG and AI agents.

---

## 📌 Overview

**AI Project Intelligence & Risk Advisor** is a full-stack AI application designed to help project teams understand and manage project information from their existing documents.

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

The system is designed to keep AI-generated information grounded in the uploaded project data rather than generating unrelated or unsupported information.

---

## ✨ Key Features

### 📄 Intelligent Document Upload

Upload project-related files such as:

- PDF
- DOCX
- TXT
- CSV

The system extracts and processes the document content automatically.

After a document is uploaded, the project analysis pipeline automatically processes the available information and updates the project intelligence.

---

### 🔎 Retrieval-Augmented Generation (RAG)

The application uses RAG to provide project-specific AI responses.

The general flow is:

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
