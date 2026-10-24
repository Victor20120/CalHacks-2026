"""Diagnosis workflow: re-check the evidence, build the CREAO inputs, run the
researcher's personal agent, and save the result to their private memory.

The backend decides what is sent to CREAO, so the evidence from the local
connector is checked again here instead of being trusted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from backend.creao.client import ANALYSIS_DEPTHS, FOCUS_AREAS
from backend.db.database import Database
from backend.knowledge_sharing.publishing import draft_from_diagnosis
from backend.memory.private_memory import PrivateMemory
from backend.orchestration.evidence_policy import (
    MAX_ITEM_CHARS,
    MAX_ITEMS,
    MAX_TOTAL_CHARS,
    path_problem,
    redact_secrets,
)


class EvidenceRejected(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class PreparedDiagnosis:
    project: str
    symptom: str
    focus_areas: list[str]
    analysis_depth: str
    evidence: list[dict[str, Any]]
    redactions: int

    def creao_inputs(self) -> dict[str, str]:
        """Inputs for a personal agent. Only the project name is sent, never a local path."""
        parts = [self.symptom]
        if self.evidence:
            parts.append(
                f"Evidence from the researcher's project ({len(self.evidence)} excerpt(s), "
                "selected and approved by the researcher; line numbers on the left):"
            )
            for item in self.evidence:
                note = ", partial" if item["truncated"] else ""
                parts.append(f"--- {item['path']} ({item['kind']}{note}) ---\n{item['excerpt']}")
        return {
            "project_path": f"{self.project} (local files are not attached; the relevant evidence is in observed_symptom)",
            "observed_symptom": "\n\n".join(parts),
            "focus_areas": ", ".join(self.focus_areas),
            "analysis_depth": self.analysis_depth,
        }


def prepare_diagnosis(
    project: str, symptom: str, focus_areas: list[str], analysis_depth: str, evidence: list[dict[str, Any]]
) -> PreparedDiagnosis:
    """Check a diagnosis request. Raises EvidenceRejected; nothing is sent if it does."""
    if re.search(r"[\\/:]", project):
        raise EvidenceRejected("The project must be a name, not a path.")
    unknown = [f for f in focus_areas if f not in FOCUS_AREAS]
    if not focus_areas or unknown:
        raise EvidenceRejected(f"focus_areas must be one or more of: {', '.join(FOCUS_AREAS)}.")
    if analysis_depth not in ANALYSIS_DEPTHS:
        raise EvidenceRejected(f"analysis_depth must be one of: {', '.join(ANALYSIS_DEPTHS)}.")
    if len(evidence) > MAX_ITEMS:
        raise EvidenceRejected(f"Send at most {MAX_ITEMS} evidence excerpts.", 413)

    redactions = 0
    checked = []
    for item in evidence:
        problem = path_problem(item["path"])
        if problem:
            raise EvidenceRejected(f"Evidence path '{item['path']}' {problem}.")
        if len(item["excerpt"]) > MAX_ITEM_CHARS:
            raise EvidenceRejected(f"Evidence '{item['path']}' is over {MAX_ITEM_CHARS} characters.", 413)
        excerpt, n = redact_secrets(item["excerpt"])
        redactions += n
        checked.append({**item, "excerpt": excerpt})
    if sum(len(i["excerpt"]) for i in checked) > MAX_TOTAL_CHARS:
        raise EvidenceRejected(f"Evidence is over {MAX_TOTAL_CHARS} characters in total.", 413)

    symptom, n = redact_secrets(symptom.strip())
    redactions += n
    return PreparedDiagnosis(
        project=project.strip(),
        symptom=symptom,
        focus_areas=list(dict.fromkeys(focus_areas)),
        analysis_depth=analysis_depth,
        evidence=checked,
        redactions=redactions,
    )


def run_diagnosis(client: Any, prepared: PreparedDiagnosis) -> dict[str, Any]:
    """Send the prepared diagnosis to CREAO and return what the dashboard shows."""
    result = client.run(prepared.creao_inputs())
    return {
        "run_id": result.run_id,
        "reply": result.reply,
        "issues": result.issues,
        "summary": result.summary,
        "analysis_ran": result.analysis_ran,
    }


def record_diagnosis(db: Database, member_id: str, project_id: str, symptom: str, diagnosis: dict[str, Any]) -> str | None:
    """Save a finished diagnosis to the researcher's private memory, and draft a
    finding they may choose to share. Returns the draft ID, or None if there is
    nothing to share. Nothing here touches the shared Super Brain."""
    memory = PrivateMemory(db, member_id)
    issues = diagnosis.get("issues") or []
    episode_id = memory.add_episode(
        project_id,
        "diagnosis",
        summary=issues[0]["title"] if issues else "Diagnosis with no issues found",
        details=json.dumps({"symptom": symptom, "reply": diagnosis["reply"], "issues": issues}),
        status="hypothesis" if diagnosis.get("analysis_ran") else "open",
        creao_run_id=diagnosis["run_id"],
    )
    draft = draft_from_diagnosis(symptom, diagnosis)
    if draft is None:
        return None
    return memory.add_draft(project_id, draft, source_creao_run_id=diagnosis["run_id"], episode_id=episode_id)
