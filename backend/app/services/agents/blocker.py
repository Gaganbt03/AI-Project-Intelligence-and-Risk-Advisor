from sqlalchemy.orm import Session

from app.services.agents.base import BaseAgent
from app.services.ai.prompts import ANALYSIS_GUIDE, BLOCKER_SYSTEM, blocker_prompt
from app.services.ai.schemas import BlockerAgentResult, BlockerSchema


class BlockerAgent(BaseAgent):
    name = "blocker"
    schema_model = BlockerAgentResult
    system_prompt = BLOCKER_SYSTEM
    default_queries = ["blockers, blocked, blocking issues, waiting on, meeting notes", "cannot proceed, stuck, rescheduled, dependencies"]

    def build_prompt(self, chunks: list[dict]) -> str:
        context = "\n\n".join(
            f"[{i + 1}] SOURCE: {(c.get('original_name') or '')} Page {c.get('page') or '?'}\n{c['text']}"
            for i, c in enumerate(chunks)
        )
        return "Identify blockers from the context (meeting notes and progress updates)." + ANALYSIS_GUIDE.format(context=context)

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