import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Project
from app.services.agents.base import BaseAgent
from app.services.ai.prompts import ANALYSIS_GUIDE, FORECAST_SYSTEM, forecast_prompt
from app.services.ai.schemas import ForecastSchema
from app.services.rag import project_document_summary


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "Not set"


class ForecastAgent(BaseAgent):
    name = "forecast"
    schema_model = ForecastSchema
    system_prompt = FORECAST_SYSTEM
    default_queries = [
        "delivery schedule, deadlines, timeline, planned dates, progress",
        "completed tasks, pending tasks, delays, blockers, dependencies",
    ]

    def build_project_facts(self, db: Session, project: Project) -> str:
        stats = project_document_summary(db, project.id)
        facts = {
            "project": project.name,
            "status": project.status,
            "priority": project.priority,
            "start_date": _fmt(project.start_date),
            "expected_end_date": _fmt(project.expected_end_date),
            "documents_uploaded": stats["documents"],
            "open_risks": stats["open_risks"],
            "open_blockers": stats["open_blockers"],
            "total_tasks": stats["total_tasks"],
            "completed_tasks": stats["completed_tasks"],
            "pending_tasks": stats["pending_tasks"],
            "task_completion_rate_pct": stats["completion_rate"],
        }
        return json.dumps(facts, indent=2)

    def run(self, db: Session, project_id: int) -> dict:
        project = db.query(Project).filter(Project.id == project_id).first()
        chunks = self.retrieve_context(project_id)
        facts = self.build_project_facts(db, project) if project else "{}"
        if not chunks:
            return {"status": "no_documents", "data": None, "sources": [], "error": None}
        prompt = forecast_prompt(self._context_text(chunks), facts)
        result = self.run_with_retry(prompt)
        return {
            "status": "ok" if result else "invalid_output",
            "data": result,
            "sources": chunks,
            "provider": getattr(result, "__provider", "") if result else "",
            "model": getattr(result, "__model", "") if result else "",
            "error": "Agent produced unusable structured output." if not result else None,
        }

    def _context_text(self, chunks: list[dict]) -> str:
        return "\n\n".join(
            f"[{i + 1}] SOURCE: {(c.get('original_name') or '')} Page {c.get('page') or '?'}\n{c['text']}"
            for i, c in enumerate(chunks)
        )