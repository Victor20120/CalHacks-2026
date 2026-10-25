"""Diagnosis workflow: re-check the evidence, build the CREAO inputs, run the
researcher's personal agent, and save the result to their private memory.

The backend decides what is sent to CREAO, so the evidence from the local
connector is checked again here instead of being trusted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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

MAX_CONTEXT_FINDINGS = 3
MAX_CONTEXT_CHARS = 4_000
MAX_CONTEXT_SUMMARY_CHARS = 1_200
KNOWN_CONTEXT_HEADER = (
    "Approved findings from this research group's Super Brain, shared by teammates. Each is labelled with "
    "its verification status; a hypothesis has not been verified by a researcher. Use them as prior "
    "evidence to check against, not as established facts, and say which ones you relied on:"
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
    known_context: str = ""  # earlier Super Brain findings, as sent to the agent
    context_finding_ids: list[str] = field(default_factory=list)
    project_context: str = ""  # from the researcher's saved project overview
    agent_rules: str = ""

    def creao_inputs(self) -> dict[str, str]:
        """Inputs for a personal agent. Only the project name is sent, never a local path."""
        parts = [self.symptom]
        if self.project_context:
            parts.append("Project context (from the researcher's saved overview of this project; "
                         f"it may be out of date):\n{self.project_context}")
        if self.evidence:
            parts.append(
                f"Evidence from the researcher's project ({len(self.evidence)} excerpt(s), "
                "selected and approved by the researcher; line numbers on the left):"
            )
            for item in self.evidence:
                note = ", partial" if item["truncated"] else ""
                parts.append(f"--- {item['path']} ({item['kind']}{note}) ---\n{item['excerpt']}")
        inputs = {
            "project_path": f"{self.project} (local files are not attached; the relevant evidence is in observed_symptom)",
            "observed_symptom": "\n\n".join(parts),
            "focus_areas": ", ".join(self.focus_areas),
            "analysis_depth": self.analysis_depth,
        }
        if self.known_context:
            inputs["known_context"] = self.known_context
        if self.agent_rules:
            from backend.orchestration.chat import with_agent_rules
            inputs = with_agent_rules(inputs, self.agent_rules)
        return inputs


def known_context_from(findings: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Format Super Brain findings for the agent's known_context input, within the
    size limits. Returns the text and the IDs of the findings actually included."""
    if not findings:
        return "", []
    text, used = KNOWN_CONTEXT_HEADER, []
    for f in findings[:MAX_CONTEXT_FINDINGS]:
        summary = f["summary"]
        if len(summary) > MAX_CONTEXT_SUMMARY_CHARS:
            summary = summary[:MAX_CONTEXT_SUMMARY_CHARS].rstrip() + " [...]"
        entry = (
            f"\n\n- [{f['verification_status']}] {f['title']} (shared by {f['contributed_by_name']} on "
            f"{f['approved_at'][:10]}, project {f['project_id']}, finding {f['id'][:8]})\n{summary}"
        )
        if len(text) + len(entry) > MAX_CONTEXT_CHARS:
            break
        text += entry
        used.append(f["id"])
    return (text, used) if used else ("", [])


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


def record_diagnosis(
    db: Database,
    member_id: str,
    project_id: str,
    symptom: str,
    diagnosis: dict[str, Any],
    context_finding_ids: list[str] | None = None,
) -> str | None:
    """Save a finished diagnosis to the researcher's private memory, and draft a
    finding they may choose to share. Returns the draft ID, or None if there is
    nothing to share. Nothing here touches the shared Super Brain. The IDs of the
    findings it was given as context are kept so approval can link to them."""
    memory = PrivateMemory(db, member_id)
    issues = diagnosis.get("issues") or []
    details = {"symptom": symptom, "reply": diagnosis["reply"], "issues": issues,
               "context_finding_ids": list(context_finding_ids or []),
               "diagnostic_graphs": diagnosis.get("diagnostic_graphs", [])}
    episode_id = memory.add_episode(
        project_id,
        "diagnosis",
        summary=issues[0]["title"] if issues else "Diagnosis with no issues found",
        details=json.dumps(details),
        status="hypothesis" if diagnosis.get("analysis_ran") else "open",
        creao_run_id=diagnosis["run_id"],
    )
    draft = draft_from_diagnosis(symptom, diagnosis)
    if draft is None:
        return None
    return memory.add_draft(project_id, draft, source_creao_run_id=diagnosis["run_id"], episode_id=episode_id)
