"""SimGuard local connector, command-line version (the dashboard replaces it in build step 6).

    python -m local_connector grant <project folder>
    python -m local_connector list
    python -m local_connector revoke <project folder>
    python -m local_connector diagnose <project folder> --symptom "..." --file run.log --file solver.py:10-40

`diagnose` shows exactly what will be sent and asks before sending it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

from local_connector.evidence.extract import Evidence, extract_evidence
from local_connector.file_access.grants import AccessDenied, GrantRegistry

POLL_SECONDS = 3
POLL_TIMEOUT_SECONDS = 360  # the backend gives CREAO up to 5 minutes


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # replies contain symbols like α
    args = _parser().parse_args(argv)
    registry = GrantRegistry()
    try:
        if args.command == "grant":
            grant = registry.grant(args.folder)
            print(f"Granted: {grant.root}\nProject name sent to SimGuard: {grant.name}")
        elif args.command == "revoke":
            print("Revoked." if registry.revoke(args.folder) else "That folder was not granted.")
        elif args.command == "list":
            grants = registry.grants()
            print("\n".join(f"{g.name}: {g.root} (since {g.granted_at})" for g in grants) or "No folders granted.")
        elif args.command == "diagnose":
            return _diagnose(registry, args)
    except (AccessDenied, ValueError) as exc:
        print(f"Refused: {exc}")
        return 2
    return 0


def _diagnose(registry: GrantRegistry, args: argparse.Namespace) -> int:
    grant = registry.grant_for_root(args.folder)
    evidence = extract_evidence(registry, grant, args.files)
    _preview(evidence, args.symptom)
    if not args.yes and input("\nSend this to SimGuard? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing was sent.")
        return 1

    payload = {"project": evidence.project, "symptom": args.symptom, "focus_areas": args.focus,
               "evidence": evidence.to_payload()}
    try:
        response = requests.post(f"{args.backend}/diagnose", json=payload, timeout=30)
    except requests.ConnectionError:
        print(f"Could not reach the backend at {args.backend}. Start it with:\n"
              "  python -m uvicorn backend.api.app:app --port 8000")
        return 1
    if response.status_code != 202:
        print(f"The backend refused the request (HTTP {response.status_code}): {response.json().get('detail')}")
        return 1
    job_id = response.json()["job_id"]
    print(f"\nSent. Diagnosis job {job_id}; waiting for the agent", end="", flush=True)

    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        job = requests.get(f"{args.backend}/diagnose/{job_id}", timeout=30).json()
        if job["status"] != "running":
            print()
            return _show(job)
        print(".", end="", flush=True)
    print(f"\nStill running after {POLL_TIMEOUT_SECONDS}s. Check later: GET {args.backend}/diagnose/{job_id}")
    return 1


def _preview(evidence: Evidence, symptom: str) -> None:
    print(f"Project: {evidence.project}")
    print(f"Symptom: {symptom}")
    print(f"Evidence ({len(evidence.items)} excerpt(s), {evidence.total_chars} characters):")
    for item in evidence.items:
        note = ", partial" if item.truncated else ""
        print(f"  - {item.path} ({item.kind}{note}, {len(item.excerpt)} characters)")
    if evidence.redactions:
        print(f"Secrets blanked out: {evidence.redactions}")
    if evidence.skipped:
        print(f"Left out (size limit reached): {', '.join(evidence.skipped)}")
    if os.environ.get("SIMGUARD_SHOW_EVIDENCE"):
        for item in evidence.items:
            print(f"\n--- {item.path} ---\n{item.excerpt}")


def _show(job: dict) -> int:
    if job["status"] == "failed":
        error = job["error"]
        print(f"Diagnosis failed ({error['type']}): {error['message']}")
        return 1
    diagnosis = job["diagnosis"]
    print(f"CREAO run {diagnosis['run_id']}")
    if diagnosis["issues"]:
        print("\nIssues (status is the agent's own label, not verified by a researcher):")
        for issue in diagnosis["issues"]:
            print(f"  [{issue['status']}, {issue['confidence'] or 'unknown'} confidence] {issue['title']}")
            print(f"      check: {issue['proposed_validation']}")
    print(f"\n{diagnosis['reply']}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m local_connector", description="SimGuard local connector")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("grant", help="allow SimGuard to read a project folder").add_argument("folder")
    sub.add_parser("revoke", help="take back access to a project folder").add_argument("folder")
    sub.add_parser("list", help="show granted project folders")
    d = sub.add_parser("diagnose", help="send selected evidence for a diagnosis")
    d.add_argument("folder", help="a granted project folder")
    d.add_argument("--symptom", required=True, help="what looked wrong")
    d.add_argument("--file", dest="files", action="append", required=True,
                   help="file to include, optionally with a line range: solver.py:10-40 (repeatable)")
    d.add_argument("--focus", help="what the agent should focus on")
    d.add_argument("--backend", default=os.environ.get("SIMGUARD_BACKEND", "http://127.0.0.1:8000"))
    d.add_argument("--yes", action="store_true", help="send without asking (after the preview)")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
