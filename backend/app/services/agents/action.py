from sqlalchemy.orm import Session

from app.services.agents.base import BaseAgent
from app.services.ai.prompts import ACTION_SYSTEM
from app.services.ai.schemas import ActionItemAgentResult


class ActionItemAgent(BaseAgent):
    name = "action"
    schema_model = ActionItemAgentResult
    system_prompt = ACTION_SYSTEM
    default_queries = [
        "action items, will do, assigned to, by Friday, to complete, responsible for",
        "to-dos, next steps, meeting decisions, commitments, follow-up",
    ]

    def build_prompt(self, chunks: list[dict]) -> str:
        context = "\n\n".join(
            f"[{i + 1}] SOURCE: {(c.get('original_name') or '')} Page {c.get('page') or '?'}\n{c['text']}"
            for i, c in enumerate(chunks)
        )
        return f"Extract action items / to-dos from the context.\n\nDOCUMENT CONTEXT (only this material is available to you):\n----\n{context}\n----"

    def run(self, db: Session, project_id: int) -> dict:
        chunks = self.retrieve_context(project_id)
        if not chunks:
            return {"status": "no_documents", "data": None, "sources": [], "error": None}
        prompt = self.build_prompt(chunks)
        result = self.run_with_retry(prompt)
        return {
            "status": "ok" if result else "invalid_output",
            "data": result,
            "sources": chunks,
            "provider": getattr(result, "__provider", "") if result else "",
            "model": getattr(result, "__model", "") if result else "",
            "error": "Agent produced unusable structured output." if not result else None,
        }