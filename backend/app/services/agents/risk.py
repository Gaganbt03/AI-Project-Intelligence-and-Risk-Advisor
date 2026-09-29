from sqlalchemy.orm import Session

from app.services.agents.base import BaseAgent
from app.services.ai.prompts import ANALYSIS_GUIDE, RISK_SYSTEM, risk_prompt
from app.services.ai.schemas import RiskAgentResult, RiskSchema


class RiskAgent(BaseAgent):
    name = "risk"
    schema_model = RiskAgentResult
    system_prompt = RISK_SYSTEM
    default_queries = [
        "risks, schedule delays, missed deadlines, dependencies, blockers",
        "unassigned responsibilities, incomplete requirements, missing documentation, resources, unresolved decisions",
    ]

    def build_prompt(self, chunks: list[dict]) -> str:
        context = "\n\n".join(
            f"[{i + 1}] SOURCE: {(c.get('original_name') or '')} Page {c.get('page') or '?'}\n{c['text']}"
            for i, c in enumerate(chunks)
        )
        return "Identify project risks from the context." + ANALYSIS_GUIDE.format(context=context)

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