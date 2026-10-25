"""Build ExpandRG's package: a wheel with the dashboard pages inside (so it needs no Node), and
the VS Code extension carrying that wheel (so Set up can install ExpandRG on any computer).

    python build_package.py          the wheel in dist/, and a copy in vscode-extension/engine/
    python build_package.py --vsix   also test and package the VS Code extension (.vsix)

Needs `npm install` in frontend/ (and in vscode-extension/ for --vsix). The wheel is checked
for files that must never ship (keys, databases) before it's copied anywhere.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES = ROOT / "expandrg" / "pages"
ENGINE = ROOT / "vscode-extension" / "engine"
NEVER_SHIP = re.compile(r"(^|/)(\.env[^/]*|group\.env|grants\.json|[^/]*\.(db|sqlite3?))$|(^|/)data/")


def version() -> str:
    text = (ROOT / "expandrg" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def run(cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"> {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def build_wheel() -> Path:
    npm = shutil.which("npm")
    if npm is None or not (ROOT / "frontend" / "node_modules").exists():
        sys.exit("Building the dashboard needs Node: install it, then run  cd frontend  and  npm install")
    run([npm, "run", "build"], ROOT / "frontend")
    shutil.rmtree(PAGES, ignore_errors=True)
    shutil.copytree(ROOT / "frontend" / "dist", PAGES)

    out = ROOT / "dist"
    for old in out.glob("expandrg-*.whl"):
        old.unlink()
    run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(out)])
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    for info in ROOT.glob("*.egg-info"):
        shutil.rmtree(info, ignore_errors=True)
    [wheel] = out.glob(f"expandrg-{version()}-*.whl")

    names = zipfile.ZipFile(wheel).namelist()
    bad = [n for n in names if NEVER_SHIP.search(n)]
    if bad:
        wheel.unlink()
        sys.exit(f"Refusing to ship {wheel.name}: it would contain {', '.join(bad)}")
    if "expandrg/pages/index.html" not in names:
        sys.exit(f"{wheel.name} has no dashboard pages")
    print(f"Built {wheel.relative_to(ROOT)} ({len(names)} files, {wheel.stat().st_size // 1024} KB)")
    return wheel


def build_vsix(wheel: Path) -> Path:
    folder = ROOT / "vscode-extension"
    package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    if package["version"] != version():
        sys.exit(f"vscode-extension/package.json is {package['version']} but expandrg is {version()}: make them match")
    npm, npx = shutil.which("npm"), shutil.which("npx")
    run([npm, "test"], folder)
    vsix = folder / f"expandrg-{version()}.vsix"
    run([npx, "--yes", "@vscode/vsce@3.2.1", "package", "--no-dependencies", "--allow-missing-repository",
         "--skip-license", "-o", vsix.name], folder)
    if f"extension/engine/{wheel.name}" not in zipfile.ZipFile(vsix).namelist():
        sys.exit(f"{vsix.name} doesn't carry {wheel.name}")
    print(f"Built vscode-extension/{vsix.name}, carrying {wheel.name}")
    return vsix


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the ExpandRG wheel, and optionally the VS Code extension.")
    parser.add_argument("--vsix", action="store_true", help="also test and package the VS Code extension")
    args = parser.parse_args()
    wheel = build_wheel()
    shutil.rmtree(ENGINE, ignore_errors=True)
    ENGINE.mkdir(parents=True)
    shutil.copy2(wheel, ENGINE / wheel.name)
    print("Copied it to vscode-extension/engine/ for the extension to install")
    if args.vsix:
        build_vsix(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
