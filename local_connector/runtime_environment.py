"""Discover and reuse an existing project's local execution environment.

Executable locations stay local. CREAO sees labels, availability, and aliases;
its plan cannot introduce an arbitrary absolute interpreter or compiler path.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from backend.orchestration.simulation_setup import InvalidSetup
from local_connector.experiments.sandbox import clean_environment

TOOLS = ("gfortran", "ifx", "ifort", "flang", "mpifort", "mpif90", "mpiifort", "mpiifx", "make",
         "mingw32-make", "cmake", "ninja", "fpm", "gcc", "g++", "clang", "clang++", "mpirun",
         "mpiexec", "julia", "Rscript", "node", "cargo", "octave")
COMPILERS = {"FC": "{fortran}", "CC": "{cc}", "CXX": "{cxx}"}
KEEP_VARIABLES = {"FC", "F77", "F90", "F95", "CC", "CXX", "FFLAGS", "FCFLAGS", "CFLAGS", "CXXFLAGS",
                  "LDFLAGS", "LIB", "LIBPATH", "INCLUDE", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
                  "CMAKE_PREFIX_PATH", "PKG_CONFIG_PATH", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV"}


def _absolute(path) -> str:
    # Keep interpreter symlinks intact: resolving .venv/bin/python can lose its venv.
    return os.path.abspath(os.path.expanduser(str(path)))


def _python_at(folder: Path) -> Path | None:
    if folder.is_file():
        return folder
    for relative in ("Scripts/python.exe", "bin/python", "bin/python3", "python.exe"):
        candidate = folder / relative
        if candidate.is_file():
            return candidate
    return None


def _find(program, path, finder):
    try:
        return finder(program, path=path)
    except TypeError:  # preserve the existing test/custom-runner injection API
        return finder(program)


def _compiler(value: str, path: str, finder, root: Path) -> tuple[str | None, list[str]]:
    value = value.strip()
    if not value or any(c in value for c in ("\n", "\x00", "|", ";")):
        return None, []
    target = Path(value.strip('"')).expanduser()
    if not target.is_absolute():
        target = root / target
    if target.is_file():
        return _absolute(target), []
    try:
        words = shlex.split(value, posix=os.name != "nt")
    except ValueError:
        return None, []
    if not words:
        return None, []
    first = words[0].strip('"')
    target = Path(first).expanduser()
    if not target.is_absolute():
        target = root / target
    executable = _absolute(target) if target.is_file() else _find(first, path, finder)
    return executable, words[1:]


def _project_compilers(root: Path) -> dict[str, str]:
    candidates = {"FC": [], "CC": [], "CXX": []}
    def read(relative):
        path = root / relative
        try:
            if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size > 200_000:
                return ""
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    for name in ("Makefile", "makefile", "GNUmakefile"):
        # A trailing "# comment" is make's, not part of the compiler (FC = gfortran  # compiler).
        for key, value in re.findall(r"^\s*(FC|CC|CXX)\s*(?:[:?+]?=)\s*([^#\n]*?)\s*(?:#.*)?$", read(name), re.M):
            if value.strip():
                candidates[key].append(value.strip())
    cmake_keys = {"CMAKE_Fortran_COMPILER": "FC", "CMAKE_C_COMPILER": "CC", "CMAKE_CXX_COMPILER": "CXX"}
    for name in ("CMakePresets.json", "CMakeUserPresets.json"):
        try:
            data = json.loads(read(name) or "{}")
            for preset in data.get("configurePresets", []):
                for key, value in preset.get("cacheVariables", {}).items():
                    if key in cmake_keys:
                        value = value.get("value") if isinstance(value, dict) else value
                        if isinstance(value, str):
                            candidates[cmake_keys[key]].append(value)
        except (ValueError, AttributeError, TypeError):
            pass
    for key, value in re.findall(r"^(CMAKE_(?:Fortran|C|CXX)_COMPILER):\w+=(.+)$", read("build/CMakeCache.txt"), re.M):
        candidates[cmake_keys[key]].append(value.strip())
    # Conflicting build presets need a choice, not a guessed compiler.
    return {key: values[0] for key, raw in candidates.items() if len(values := list(dict.fromkeys(raw))) == 1}


def discover_runtime(root: Path, environment: str | None = None, compiler: str | None = None, *,
                     environ: dict | None = None, find_program=shutil.which) -> dict:
    """Choose an existing environment; create/install nothing and read no credentials."""
    root = Path(root).resolve()
    inherited = dict(os.environ if environ is None else environ)
    selected, prefix, label, kind = None, None, "Current environment", "current"
    if environment and environment.strip():
        target = Path(environment.strip().strip('"')).expanduser()
        if not target.is_absolute():
            target = root / target
        selected = _python_at(target)
        if selected is None:
            raise InvalidSetup("That existing environment does not contain a Python interpreter. Choose its folder or Python executable.")
        prefix = target if target.is_dir() else selected.parent.parent if selected.parent.name.lower() in ("bin", "scripts") else selected.parent
        label, kind = f"Existing environment: {prefix.name}", "selected"
    if selected is None:
        for name in (".venv", "venv", "env"):
            target = root / name
            found = _python_at(target)
            if found:
                selected, prefix, label, kind = found, target, f"Project {name}", "project"
                break
    if selected is None:
        for key in ("VIRTUAL_ENV", "CONDA_PREFIX"):
            if inherited.get(key):
                target = Path(inherited[key]).expanduser()
                found = _python_at(target)
                if found:
                    selected, prefix = found, target
                    label, kind = f"Active {target.name}", "conda" if key == "CONDA_PREFIX" else "active"
                    break
    if selected is None:
        selected = Path(sys.executable)
        candidate = selected.parent.parent if selected.parent.name.lower() in ("bin", "scripts") else selected.parent
        if (candidate / "pyvenv.cfg").is_file() or (candidate / "conda-meta").is_dir() or candidate == Path(sys.prefix) and sys.prefix != sys.base_prefix:
            prefix, label = candidate, f"Current {candidate.name}"
    is_conda = bool(prefix and (prefix / "conda-meta").is_dir()) or kind == "conda"
    dirs = []
    if prefix:
        dirs.extend(str(prefix / p) for p in ("Scripts", "bin", "Library/bin", "Library/mingw-w64/bin", "Library/usr/bin"))
        dirs.append(str(prefix))
    dirs.extend(str(root / p) for p in ("bin", "tools/bin"))
    dirs.extend(inherited.get("PATH", "").split(os.pathsep))
    search = os.pathsep.join(dict.fromkeys(p for p in dirs if p))
    configured = {**_project_compilers(root), **{k: inherited[k] for k in COMPILERS if inherited.get(k)}}
    variables = {k: v for k, v in inherited.items() if k in KEEP_VARIABLES}
    variables.update(configured)
    if prefix:
        variables.pop("VIRTUAL_ENV" if is_conda else "CONDA_PREFIX", None)
        if not is_conda:
            variables.pop("CONDA_DEFAULT_ENV", None)
        variables["CONDA_PREFIX" if is_conda else "VIRTUAL_ENV"] = _absolute(prefix)
    tools = {name: result for name in TOOLS if (result := _find(name, search, find_program))}
    flags = {}
    for key, alias in COMPILERS.items():
        executable, extra = _compiler(configured.get(key, ""), search, find_program, root)
        if executable:
            tools[alias], flags[alias] = executable, extra
            basename = Path(executable).stem.lower()
            for name in TOOLS:
                if name.lower() == basename:
                    tools[name] = executable
    if compiler and compiler.strip():
        target = Path(compiler.strip().strip('"')).expanduser()
        if not target.is_absolute():
            target = root / target
        if not target.is_file():
            raise InvalidSetup("That compiler executable was not found. Select its existing executable path.")
        tools["{fortran}"] = _absolute(target)
        flags["{fortran}"] = []
        variables["FC"] = subprocess.list2cmdline([_absolute(target)]) if os.name == "nt" else shlex.quote(_absolute(target))
        for name in TOOLS:
            if name.lower() == target.stem.lower():
                tools[name] = _absolute(target)
    if "{fortran}" not in tools:
        for name in ("gfortran", "ifx", "ifort", "flang", "mpifort", "mpif90", "mpiifort", "mpiifx"):
            if name in tools:
                tools["{fortran}"] = tools[name]
                break
    for alias, choices in (("{cc}", ("gcc", "clang")), ("{cxx}", ("g++", "clang++"))):
        if alias not in tools:
            for name in choices:
                if name in tools:
                    tools[alias] = tools[name]
                    break
    compiler_dirs = [str(Path(p).parent) for p in tools.values() if Path(p).is_absolute()]
    search = os.pathsep.join(dict.fromkeys([*compiler_dirs, *search.split(os.pathsep)]))
    return {"label": label, "kind": kind, "python": _absolute(selected), "path": search,
            "tools": tools, "flags": flags, "variables": clean_environment(variables), "source_root": str(root)}


def runtime_facts(runtime: dict) -> dict:
    """Read metadata with the selected interpreter, not ExpandRG's interpreter.

    This isolated metadata probe imports no simulation/project modules. Failure
    means dependency availability is unknown, not that a package is missing.
    """
    probe = ("import sys,json,importlib.util; print(json.dumps({'python_version':sys.version.split()[0],"
             "'python_packages':{n:importlib.util.find_spec(n) is not None for n in "
             "('numpy','scipy','matplotlib','pandas')}}))")
    facts = {"system": platform.system(), "environment": runtime["label"], "reuse_existing_environment": True,
             "tools": {name: bool(runtime["tools"].get(name)) for name in TOOLS},
             "preferred_compilers": {name: alias for name, alias in (("Fortran", "{fortran}"), ("C", "{cc}"), ("C++", "{cxx}"))
                                     if alias in runtime["tools"]},
             "python_version": "unknown", "python_packages": {}, "environment_probe": "unknown"}
    try:
        result = subprocess.run([runtime["python"], "-I", "-c", probe], capture_output=True, text=True,
                                timeout=8, env=runtime_environ(runtime))
        if result.returncode == 0 and len(result.stdout) < 8000:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and isinstance(data.get("python_packages"), dict):
                facts.update(python_version=str(data.get("python_version", "unknown"))[:40],
                             python_packages={k: v for k, v in data["python_packages"].items()
                                              if k in ("numpy", "scipy", "matplotlib", "pandas") and isinstance(v, bool)},
                             environment_probe="checked")
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return facts


def runtime_environ(runtime: dict) -> dict:
    selected = dict(os.environ)
    # Activation markers belong to the selected runtime, not the connector's
    # parent shell. In particular a Conda prefix must not leak into a venv run.
    for key in ("VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV"):
        selected.pop(key, None)
    selected.update(runtime.get("variables", {}))
    selected.update(PATH=runtime["path"], PYTHONDONTWRITEBYTECODE="1")
    if runtime.get("kind") != "current":
        selected.pop("PYTHONHOME", None)
    return clean_environment(selected)


def missing_runtime_tools(plan: dict, runtime: dict, find_program=shutil.which) -> list[str]:
    missing = []
    for command in [*plan["build_commands"], plan["run_command"]]:
        if not command:
            continue
        programs = [command[0], *(alias for alias in COMPILERS.values()
                                  if any(alias in argument for argument in command[1:]))]
        for program in programs:
            if program == "{python}":
                if not Path(runtime["python"]).is_file():
                    missing.append("Selected Python environment")
            elif "/" not in program and "\\" not in program:
                location = runtime["tools"].get(program)
                if location:
                    if Path(location).is_absolute() and not Path(location).is_file():
                        missing.append(program)
                elif program in COMPILERS.values() or not _find(program, runtime["path"], find_program):
                    missing.append(program)
    return sorted(set(missing))


def resolve_runtime_command(command: list[str], cwd: Path, work_root: Path, runtime: dict,
                            find_program=shutil.which) -> list[str]:
    program, *arguments = command
    if program == "{python}":
        executable, flags = runtime["python"], []
    elif program in runtime["tools"]:
        executable, flags = runtime["tools"][program], runtime.get("flags", {}).get(program, [])
    elif "/" in program or "\\" in program:
        target = (cwd / program).resolve()
        if not target.is_relative_to(work_root.resolve()):
            raise InvalidSetup("The executable must remain inside this execution copy.")
        executable, flags = str(target), []
    else:
        executable, flags = _find(program, runtime["path"], find_program), []
        if not executable:
            raise InvalidSetup(f"{program} is not connected in the selected environment.")
    for index, argument in enumerate(arguments):
        for alias in ("{fortran}", "{cc}", "{cxx}"):
            if alias in argument:
                if alias not in runtime["tools"]:
                    raise InvalidSetup(f"Connect the existing compiler for {alias}.")
                location = runtime["tools"][alias]
                if argument.startswith(("FC=", "CC=", "CXX=")):
                    words = [location, *runtime.get("flags", {}).get(alias, [])]
                    location = subprocess.list2cmdline(words) if os.name == "nt" else shlex.join(words)
                argument = argument.replace(alias, location)
        arguments[index] = argument
    return [str(executable), *flags, *arguments]
