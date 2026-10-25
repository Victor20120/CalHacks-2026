"""Set up a research group in ExpandRG using agents already created in CREAO.

    python setup_expandrg.py

Asks, in this terminal: whether you're the group leader; then the group's name, the Super
Brain's CREAO app ID and key, each researcher's name and their agent's app ID and key, and
the first project. Keys are typed hidden, checked with CREAO (no credits) and saved only on
this computer. The demo group stays as it is. Start the new group afterwards with:

    python run_demo.py --group <id>

Add another project to a group set up on this computer (asks which group, the project's
name and who works on it):

    python setup_expandrg.py --add-project

Installed as a package, the same is `expandrg setup` / `expandrg add-project` (see expandrg/setup_group.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from expandrg.setup_group import main  # noqa: E402  (needs the repo on the path)

if __name__ == "__main__":
    raise SystemExit(main())
