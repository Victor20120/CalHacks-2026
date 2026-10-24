"""Diagnosis workflow: re-check the evidence, build the CREAO inputs, run the agent.

The backend decides what is sent to CREAO, so the evidence from the local
connector is checked again here instead of being trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.orchestration.evidence_policy import (
    MAX_ITEM_CHARS,
    MAX_ITEMS,
    MAX_TOTAL_CHARS,
    path_problem,
    redact_secrets,
)

DEFAULT_FOCUS_AREAS = "numerical stability; most likely root cause; how to validate it"


class EvidenceRejected(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class PreparedDiagnosis:
    project: str
    symptom: str
    focus_areas: str
    evidence: list[dict[str, Any]]
    redactions: int

    def creao_inputs(self) -> dict[str, str]:
        """Inputs for the hub agent. Only the project name is sent, never a local path."""
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
            "focus_areas": self.focus_areas,
        }


def prepare_diagnosis(
    project: str, symptom: str, focus_areas: str | None, evidence: list[dict[str, Any]]
) -> PreparedDiagnosis:
    """Check a diagnosis request. Raises EvidenceRejected; nothing is sent if it does."""
    if re.search(r"[\\/:]", project):
        raise EvidenceRejected("The project must be a name, not a path.")
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
        focus_areas=(focus_areas or "").strip() or DEFAULT_FOCUS_AREAS,
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
