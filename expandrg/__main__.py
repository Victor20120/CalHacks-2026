"""The expandrg command (also `python -m expandrg`).

    expandrg start [--group ID] [--no-browser] [--fresh] [--check]
                              start ExpandRG: the demo group, or a research group made with setup
    expandrg stop             stop ExpandRG, wherever it was started
    expandrg setup            set up a research group (the group leader, once)
    expandrg add-project      add a project to a group set up on this computer
    expandrg version
"""
from __future__ import annotations

import argparse

from expandrg import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="expandrg", description="ExpandRG: your research group's Super Brain.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    start = commands.add_parser("start", help="start ExpandRG (the demo group, or --group ID)")
    start.add_argument("--group", metavar="ID", help="a research group made with: expandrg setup")
    start.add_argument("--no-browser", action="store_true", help="don't open dashboard windows")
    start.add_argument("--fresh", action="store_true", help="reset the demo data first (deletes local demo databases)")
    start.add_argument("--check", action="store_true", help="start everything, check it, then stop")
    commands.add_parser("stop", help="stop ExpandRG, wherever it was started")
    commands.add_parser("setup", help="set up a research group (the group leader, once)")
    commands.add_parser("add-project", help="add a project to a group set up on this computer")
    commands.add_parser("version", help="show the installed version")
    args = parser.parse_args(argv)

    if args.command == "version":
        print(f"ExpandRG {__version__}")
        return 0
    if args.command in ("setup", "add-project"):
        from expandrg.setup_group import interactive, run, run_add_project
        return interactive(run if args.command == "setup" else run_add_project)
    from expandrg.launcher import start as start_expandrg, stop_running
    if args.command == "stop":
        return stop_running()
    return start_expandrg(group=args.group, fresh=args.fresh, check=args.check, no_browser=args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
