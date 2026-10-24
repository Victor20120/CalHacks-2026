"""ExpandRG local connector, command-line version (the dashboard replaces it in build step 6).

    python -m local_connector grant <project folder>
    python -m local_connector list
    python -m local_connector revoke <project folder>
    python -m local_connector diagnose <folder> --as researcher_a --project heat-diffusion --symptom "..." --file run.log
    python -m local_connector drafts --as researcher_a
    python -m local_connector approve <draft id> --as researcher_a [--title ...] [--summary ...] [--status ...]
    python -m local_connector reject <draft id> --as researcher_a
    python -m local_connector findings --as researcher_a --project heat-diffusion
    python -m local_connector ask "blows up after refining the grid" --as researcher_b
    python -m local_connector finding <finding id> --as researcher_b
    python -m local_connector watch <folder> --file run.log --as researcher_a --project heat-diffusion
    python -m local_connector overview <folder> --as researcher_a --project my-research
    python -m local_connector serve        (the local service the dashboard talks to)

`diagnose` shows exactly what will be sent and asks before sending it, and
gives your agent earlier approved findings from the Super Brain unless you
pass --no-super-brain. Drafts stay private until you approve them. --as
defaults to $EXPANDRG_MEMBER.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

from backend.creao.client import ANALYSIS_DEPTHS, FOCUS_AREAS
from backend.db.schema import VERIFICATION_STATUSES
from local_connector.evidence.extract import Evidence, extract_evidence
from local_connector.file_access.grants import AccessDenied, GrantRegistry

POLL_SECONDS = 3
POLL_TIMEOUT_SECONDS = 360  # the backend gives CREAO up to 5 minutes


class BackendError(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # replies contain symbols like α
    args = _parser().parse_args(argv)
    registry = GrantRegistry()
    try:
        if args.command == "grant":
            grant = registry.grant(args.folder)
            print(f"Granted: {grant.root}\nProject name sent to ExpandRG: {grant.name}")
        elif args.command == "revoke":
            print("Revoked." if registry.revoke(args.folder) else "That folder was not granted.")
        elif args.command == "list":
            grants = registry.grants()
            print("\n".join(f"{g.name}: {g.root} (since {g.granted_at})" for g in grants) or "No folders granted.")
        elif args.command == "diagnose":
            return _diagnose(registry, args)
        elif args.command == "drafts":
            _drafts(args)
        elif args.command == "approve":
            edits = {"title": args.title, "summary": args.summary, "verification_status": args.status}
            finding = _call(args, "POST", f"/drafts/{args.draft_id}/approve", json=edits)
            print(f"Shared to the Super Brain as finding {finding['id']} ({finding['verification_status']}):")
            print(f"  {finding['title']}")
        elif args.command == "reject":
            print(_call(args, "POST", f"/drafts/{args.draft_id}/reject")["detail"])
        elif args.command == "findings":
            _findings(args)
        elif args.command == "ask":
            _ask(args)
        elif args.command == "finding":
            _finding(args)
        elif args.command == "overview":
            return _overview(registry, args)
        elif args.command == "watch":
            return _watch(registry, args)
        elif args.command == "serve":
            import uvicorn

            from local_connector.service import create_service

            print(f"Local connector for the dashboard on http://127.0.0.1:{args.port} (this computer only)")
            uvicorn.run(create_service(registry, args.backend), host="127.0.0.1", port=args.port, log_level="warning")
    except (AccessDenied, ValueError) as exc:
        print(f"Refused: {exc}")
        return 2
    except BackendError as exc:
        print(exc)
        return 1
    return 0


def _diagnose(registry: GrantRegistry, args: argparse.Namespace) -> int:
    grant = registry.grant_for_root(args.folder)
    evidence = extract_evidence(registry, grant, args.files)
    _preview(evidence, args)
    if not args.yes and input("\nSend this to ExpandRG? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing was sent.")
        return 1

    payload = {"project_id": args.project, "symptom": args.symptom, "focus_areas": args.focus or ["numerical"],
               "analysis_depth": args.depth, "evidence": evidence.to_payload(),
               "use_super_brain": not args.no_super_brain}
    headers = _sign_in(args)
    started = _call(args, "POST", "/diagnose", json=payload, headers=headers)
    job_id = started["job_id"]
    if started["prior_findings"]:
        print(f"\nYour agent also gets {len(started['prior_findings'])} earlier finding(s) from the Super Brain:")
        for f in started["prior_findings"]:
            print(f"  - [{f['verification_status']}] {f['title']} (shared by {f['contributed_by_name']})")
    print(f"\nSent. Diagnosis job {job_id}; waiting for your agent", end="", flush=True)

    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        job = _call(args, "GET", f"/diagnose/{job_id}", headers=headers)
        if job["status"] != "running":
            print()
            return _show(job, args)
        print(".", end="", flush=True)
    print(f"\nStill running after {POLL_TIMEOUT_SECONDS}s. Check later: GET {args.backend}/diagnose/{job_id}")
    return 1


def _preview(evidence: Evidence, args: argparse.Namespace) -> None:
    print(f"Signed in as: {args.member}   Project: {args.project}")
    print(f"Symptom: {args.symptom}")
    print(f"Evidence ({len(evidence.items)} excerpt(s), {evidence.total_chars} characters):")
    for item in evidence.items:
        note = ", partial" if item.truncated else ""
        print(f"  - {item.path} ({item.kind}{note}, {len(item.excerpt)} characters)")
    if evidence.redactions:
        print(f"Secrets blanked out: {evidence.redactions}")
    if evidence.skipped:
        print(f"Left out (size limit reached): {', '.join(evidence.skipped)}")
    if os.environ.get("EXPANDRG_SHOW_EVIDENCE"):
        for item in evidence.items:
            print(f"\n--- {item.path} ---\n{item.excerpt}")


def _show(job: dict, args: argparse.Namespace) -> int:
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
    if job.get("warning"):
        print(f"\nWarning: {job['warning']}")
    if job.get("draft_id"):
        print(f"\nSaved to your private memory. A draft finding was prepared; it stays private unless you approve it:")
        print(f"  python -m local_connector drafts --as {args.member}")
    return 0


def _drafts(args: argparse.Namespace) -> None:
    drafts = _call(args, "GET", "/drafts", headers=_sign_in(args))
    if not drafts:
        print("No pending drafts.")
    for d in drafts:
        print(f"\nDraft {d['id']}  [{d['project_id']}, {d['category']}, {d['verification_status']}]")
        print(f"Title: {d['title']}\n{d['summary']}")
    if drafts:
        print(f"\nShare one:  python -m local_connector approve <draft id> --as {args.member} [--title ...] [--summary ...]")
        print(f"Keep private: python -m local_connector reject <draft id> --as {args.member}")


def _findings(args: argparse.Namespace) -> None:
    findings = _call(args, "GET", f"/projects/{args.project}/findings", headers=_sign_in(args))
    if not findings:
        print("No shared findings in this project yet.")
    for f in findings:
        edited = ", edited before approval" if f["edited_before_approval"] else ""
        print(f"\n{f['title']}  [{f['category']}, {f['verification_status']}]")
        print(f"Shared by {f['contributed_by_name']} on {f['approved_at']}{edited}; CREAO run {f['source_creao_run_id']}")
        print(f["summary"])


def _ask(args: argparse.Namespace) -> None:
    params = {"q": args.query, **({"project_id": args.project} if args.project else {})}
    results = _call(args, "GET", "/superbrain/search", params=params)
    if not results:
        print("No matching findings in the projects you belong to.")
    for f in results:
        print(f"\n{f['title']}  [{f['project_id']}, {f['verification_status']}, match score {f['score']}]")
        print(f"Finding {f['id']}, shared by {f['contributed_by_name']} on {f['approved_at']}")


def _finding(args: argparse.Namespace) -> None:
    detail = _call(args, "GET", f"/findings/{args.finding_id}")
    f = detail["finding"]
    print(f"{f['title']}  [{f['project_id']}, {f['category']}, {f['verification_status']}]")
    print(f"Shared by {f['contributed_by_name']} on {f['approved_at']}; CREAO run {f['source_creao_run_id']}\n")
    print(f["summary"])
    if detail["related"]:
        print("\nLinked findings:")
        for link in detail["related"]:
            other = link["finding"]
            arrow = "->" if link["direction"] == "outgoing" else "<-"
            print(f"  {arrow} {link['relation']}: {other['title']} (finding {other['id']}, {other['project_id']})")


def _overview(registry: GrantRegistry, args: argparse.Namespace) -> int:
    """Map the whole project, show what will be sent, then have your agent explain it."""
    from local_connector.evidence.project_map import build_project_map

    grant = registry.grant_for_root(args.folder)
    project_map = build_project_map(registry, grant)
    print(f"Signed in as: {args.member}   Project: {args.project}")
    print(f"Project map: {project_map.stats['files']} files, {project_map.total_chars} characters to send")
    left_out = project_map.stats["left_out_by_rule"]
    if left_out:
        print("Never included: " + ", ".join(f"{n} {why}" for why, n in left_out.items()))
    print("Key files (only their start is sent):")
    for item in project_map.items:
        print(f"  - {item.path} ({item.kind}, {len(item.excerpt)} characters)")
    if project_map.redactions:
        print(f"Secrets blanked out: {project_map.redactions}")
    print("\nFolder tree (first lines):\n" + "\n".join(project_map.tree.splitlines()[:25]))
    if os.environ.get("EXPANDRG_SHOW_EVIDENCE"):
        for item in project_map.items:
            print(f"\n--- {item.path} ---\n{item.excerpt}")
    if not args.yes and input("\nSend this map to your agent? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing was sent.")
        return 1

    headers = _sign_in(args)
    started = _call(args, "POST", "/overview", json={"project_id": args.project, **project_map.to_payload()},
                    headers=headers)
    print(f"\nSent. Overview job {started['job_id']}; waiting for your agent", end="", flush=True)
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        job = _call(args, "GET", f"/jobs/{started['job_id']}", headers=headers)
        if job["status"] != "running":
            print()
            if job["status"] == "failed":
                print(f"Overview failed ({job['error']['type']}): {job['error']['message']}")
                return 1
            _show_overview(job["diagnosis"])
            print("\nSaved to your private memory. Later diagnoses on this project include it automatically.")
            return 0
        print(".", end="", flush=True)
    print(f"\nStill running after {POLL_TIMEOUT_SECONDS}s.")
    return 1


def _show_overview(result: dict) -> None:
    overview = result.get("overview")
    print(f"CREAO run {result['run_id']}")
    if overview:
        print(f"\nPurpose: {overview['purpose']}")
        if overview["structure"]:
            print("\nStructure:")
            for s in overview["structure"]:
                flag = "" if s["in_project"] else "   (not found in the project)"
                print(f"  {s['path']}: {s['role']}{flag}")
        if overview["how_to_run"]:
            print(f"\nHow to run: {overview['how_to_run']}")
        if overview["key_parameters"]:
            print("\nKey parameters:")
            for p in overview["key_parameters"]:
                print(f"  {p['name']} = {p['value']}  {p['meaning']}")
        if overview["open_questions"]:
            print("\nOpen questions:")
            for q in overview["open_questions"]:
                print(f"  - {q}")
    print(f"\n{result['reply']}")


def _watch(registry: GrantRegistry, args: argparse.Namespace) -> int:
    """Watch a run log in the foreground. Only reads the log; never touches the simulation."""
    from local_connector.service import BackendPoster
    from local_connector.simulation_monitor.watcher import LogWatcher

    grant = registry.grant_for_root(args.folder)
    resolved, relative = registry.check_path(args.file, grant)
    poster = BackendPoster(args.backend, args.member, args.project, relative, requests.Session()) if args.member else None

    def on_alerts(alerts):
        for a in alerts:
            print(f"[{a.severity.upper()}] {a.message}", flush=True)
        if poster:
            poster(alerts)

    watcher = LogWatcher(resolved, on_alerts, on_samples=poster.samples if poster else None,
                         on_restart=poster.new_run if poster else None)
    print(f"Watching {relative} (Ctrl+C to stop watching; the simulation is never touched)", flush=True)
    if poster:
        poster.start()
        print("The run shows live on the dashboard for you and your group leader (the log file stays here).",
              flush=True)
    try:
        while True:
            watcher.poll_once()
            time.sleep(watcher.poll_seconds)
    except KeyboardInterrupt:
        if poster:
            poster.stopped()
        print(f"\nStopped watching after {watcher.lines_read} lines and {len(watcher.alerts)} alert(s).")
    return 0


def _sign_in(args: argparse.Namespace) -> dict[str, str]:
    if not args.member:
        raise BackendError("Say who you are: --as <member id> (or set EXPANDRG_MEMBER).")
    token = _call(args, "POST", "/sign-in", json={"member_id": args.member})["token"]
    return {"Authorization": f"Bearer {token}"}


def _call(args: argparse.Namespace, method: str, path: str, **kwargs) -> dict:
    if "headers" not in kwargs and path != "/sign-in":
        kwargs["headers"] = _sign_in(args)
    try:
        response = requests.request(method, f"{args.backend}{path}", timeout=30, **kwargs)
    except requests.ConnectionError:
        raise BackendError(f"Could not reach the backend at {args.backend}. Start it with:\n"
                           "  python -m uvicorn backend.api.app:app --port 8000") from None
    if not response.ok:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:200]
        raise BackendError(f"The backend refused (HTTP {response.status_code}): {detail}")
    return response.json()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m local_connector", description="ExpandRG local connector")
    sub = parser.add_subparsers(dest="command", required=True)
    backend = argparse.ArgumentParser(add_help=False)
    backend.add_argument("--as", dest="member", default=os.environ.get("EXPANDRG_MEMBER"),
                         help="your member ID, e.g. researcher_a")
    backend.add_argument("--backend", default=os.environ.get("EXPANDRG_BACKEND", "http://127.0.0.1:8000"))

    sub.add_parser("grant", help="allow ExpandRG to read a project folder").add_argument("folder")
    sub.add_parser("revoke", help="take back access to a project folder").add_argument("folder")
    sub.add_parser("list", help="show granted project folders")

    d = sub.add_parser("diagnose", parents=[backend], help="send selected evidence for a diagnosis")
    d.add_argument("folder", help="a granted project folder")
    d.add_argument("--project", required=True, help="ExpandRG project ID, e.g. heat-diffusion")
    d.add_argument("--symptom", required=True, help="what looked wrong")
    d.add_argument("--file", dest="files", action="append", required=True,
                   help="file to include, optionally with a line range: solver.py:10-40 (repeatable)")
    d.add_argument("--focus", action="append", choices=FOCUS_AREAS, help="focus area (repeatable; default numerical)")
    d.add_argument("--depth", choices=ANALYSIS_DEPTHS, default="standard")
    d.add_argument("--no-super-brain", action="store_true", help="don't give your agent earlier findings")
    d.add_argument("--yes", action="store_true", help="send without asking (after the preview)")

    sub.add_parser("drafts", parents=[backend], help="your private drafts waiting for a decision")
    a = sub.add_parser("approve", parents=[backend], help="share a draft to the Super Brain")
    a.add_argument("draft_id")
    a.add_argument("--title", help="replace the draft's title")
    a.add_argument("--summary", help="replace the draft's summary")
    a.add_argument("--status", choices=VERIFICATION_STATUSES, help="verification status (default: the draft's)")
    r = sub.add_parser("reject", parents=[backend], help="keep a draft private for good")
    r.add_argument("draft_id")
    f = sub.add_parser("findings", parents=[backend], help="approved findings in a project")
    f.add_argument("--project", required=True)
    q = sub.add_parser("ask", parents=[backend], help="search the Super Brain (your projects only)")
    q.add_argument("query")
    q.add_argument("--project", help="only this project")
    o = sub.add_parser("finding", parents=[backend], help="one finding and its links")
    o.add_argument("finding_id")
    ov = sub.add_parser("overview", parents=[backend], help="have your agent explain a whole project (you preview first)")
    ov.add_argument("folder", help="a granted project folder")
    ov.add_argument("--project", required=True, help="ExpandRG project ID")
    ov.add_argument("--yes", action="store_true", help="send without asking (after the preview)")
    w = sub.add_parser("watch", parents=[backend], help="watch a run log for anomalies (read-only)")
    w.add_argument("folder", help="a granted project folder")
    w.add_argument("--file", required=True, help="the log to watch, e.g. run.log")
    w.add_argument("--project", required=True, help="ExpandRG project ID")
    s = sub.add_parser("serve", parents=[backend], help="run the local service the dashboard talks to")
    s.add_argument("--port", type=int, default=8001)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
