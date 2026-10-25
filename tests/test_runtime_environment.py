"""Reuse the research project's installed environment and trusted local tools."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.orchestration.simulation_setup import InvalidSetup

from local_connector.runtime_environment import (
    discover_runtime,
    missing_runtime_tools,
    resolve_runtime_command,
    runtime_environ,
    runtime_facts,
)


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test executable placeholder")
    path.chmod(0o755)
    return path.resolve()


def environment_python(prefix: Path) -> Path:
    return executable(prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def no_program(_name, path=None):
    return None


def test_project_environment_interpreter_is_reused_outside_the_saved_work_copy(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    python = environment_python(project / ".venv")
    work = tmp_path / "saved-version" / "work"
    work.mkdir(parents=True)
    (work / "solver.py").write_text("print('existing solver')")

    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=no_program)
    command = resolve_runtime_command(["{python}", "solver.py", "input.json"], work, work, runtime,
                                      find_program=no_program)

    assert Path(runtime["python"]).resolve() == python
    assert Path(command[0]).resolve() == python
    assert command[1:] == ["solver.py", "input.json"]
    assert not (work / ".venv").exists()  # reuse the original environment, don't manufacture another
    assert Path(runtime["source_root"]).resolve() == project.resolve()


def test_explicit_environment_selection_wins_over_a_project_environment(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    environment_python(project / ".venv")
    selected = tmp_path / "existing-environments" / "research-tools"
    selected_python = environment_python(selected)

    runtime = discover_runtime(project, environment=str(selected), environ={"PATH": ""}, find_program=no_program)

    assert Path(runtime["python"]).resolve() == selected_python
    assert selected.name in runtime["label"]


def test_an_existing_interpreter_path_can_be_selected_directly(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    python = environment_python(tmp_path / "existing environment")

    runtime = discover_runtime(project, environment=str(python), environ={"PATH": ""}, find_program=no_program)

    assert Path(runtime["python"]).resolve() == python


def test_relative_explicit_environment_and_compiler_paths_use_project_root(tmp_path, monkeypatch):
    project = tmp_path / "research"
    project.mkdir()
    python = environment_python(project / ".venv")
    compiler = executable(project / "tools" / "gfortran.exe")
    connector = tmp_path / "connector"
    connector.mkdir()
    monkeypatch.chdir(connector)

    runtime = discover_runtime(project, environment=".venv", compiler="tools/gfortran.exe",
                               environ={"PATH": ""}, find_program=no_program)

    assert Path(runtime["python"]).resolve() == python
    command = resolve_runtime_command(["{fortran}", "solver.f90"], project, project, runtime,
                                      find_program=no_program)
    assert Path(command[0]).resolve() == compiler


def test_an_invalid_explicit_selection_does_not_silently_use_expandrg_python(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    environment_python(project / ".venv")

    with pytest.raises(InvalidSetup, match="Python interpreter"):
        discover_runtime(project, environment=str(tmp_path / "not-an-environment"), environ={"PATH": ""},
                         find_program=no_program)


def test_activated_conda_environment_is_used_when_the_project_has_no_local_environment(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    prefix = tmp_path / "conda" / "research-environment"
    (prefix / "conda-meta").mkdir(parents=True)
    # Conda uses python.exe at the prefix root on Windows, unlike a venv.
    python = executable(prefix / "python.exe") if os.name == "nt" else environment_python(prefix)

    runtime = discover_runtime(project, environ={"PATH": "", "CONDA_PREFIX": str(prefix)}, find_program=no_program)

    assert Path(runtime["python"]).resolve() == python
    assert prefix.name in runtime["label"]


def test_explicit_conda_selection_replaces_inherited_environment_metadata(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    prefix = tmp_path / "conda" / "chosen-research-environment"
    (prefix / "conda-meta").mkdir(parents=True)
    python = executable(prefix / "python.exe") if os.name == "nt" else environment_python(prefix)

    runtime = discover_runtime(project, environment=str(prefix), environ={
        "PATH": "", "CONDA_PREFIX": str(tmp_path / "old-conda-environment"),
        "VIRTUAL_ENV": str(tmp_path / "old-venv"),
    }, find_program=no_program)

    assert Path(runtime["python"]).resolve() == python
    assert Path(runtime["variables"]["CONDA_PREFIX"]).resolve() == prefix.resolve()
    assert "VIRTUAL_ENV" not in runtime["variables"]


@pytest.mark.parametrize("selected_kind", ["venv", "conda"])
def test_execution_environment_does_not_restore_stale_activation_markers(tmp_path, monkeypatch, selected_kind):
    project = tmp_path / "research"
    project.mkdir()
    prefix = tmp_path / "selected-environment"
    if selected_kind == "conda":
        (prefix / "conda-meta").mkdir(parents=True)
        executable(prefix / "python.exe") if os.name == "nt" else environment_python(prefix)
        inherited = {"VIRTUAL_ENV": str(tmp_path / "previous-venv")}
        selected_key = "CONDA_PREFIX"
        absent_keys = ("VIRTUAL_ENV",)
    else:
        environment_python(prefix)
        inherited = {"CONDA_PREFIX": str(tmp_path / "previous-conda"), "CONDA_DEFAULT_ENV": "previous-conda"}
        selected_key = "VIRTUAL_ENV"
        absent_keys = ("CONDA_PREFIX", "CONDA_DEFAULT_ENV")
    for key in ("VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV"):
        monkeypatch.delenv(key, raising=False)
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    runtime = discover_runtime(project, environment=str(prefix), environ={"PATH": "", **inherited},
                               find_program=no_program)

    execution_environment = runtime_environ(runtime)

    assert Path(execution_environment[selected_key]).resolve() == prefix.resolve()
    assert all(key not in execution_environment for key in absent_keys)


@pytest.mark.parametrize("environment_kind", ["venv", "conda"])
def test_direct_environment_interpreter_launch_finds_tools_without_activation(tmp_path, monkeypatch,
                                                                            environment_kind):
    project = tmp_path / "research"
    project.mkdir()
    prefix = tmp_path / "installed-environments" / environment_kind
    if environment_kind == "conda":
        (prefix / "conda-meta").mkdir(parents=True)
        python = executable(prefix / "python.exe") if os.name == "nt" else environment_python(prefix)
        compiler_dir = prefix / ("Library/bin" if os.name == "nt" else "bin")
    else:
        python = environment_python(prefix)
        (prefix / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
        compiler_dir = python.parent
    compiler = executable(compiler_dir / ("gfortran.exe" if os.name == "nt" else "gfortran"))
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base-python"))

    def find_program(name, path=None):
        if name == "gfortran" and str(compiler_dir) in (path or "").split(os.pathsep):
            return str(compiler)
        return None

    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=find_program)
    command = resolve_runtime_command(["{fortran}", "solver.f90"], project, project, runtime,
                                      find_program=find_program)

    assert Path(runtime["python"]).resolve() == python
    assert Path(command[0]).resolve() == compiler
    environment_key = "CONDA_PREFIX" if environment_kind == "conda" else "VIRTUAL_ENV"
    assert Path(runtime["variables"][environment_key]).resolve() == prefix.resolve()


def test_current_interpreter_is_the_fallback_for_an_already_activated_installation(tmp_path):
    project = tmp_path / "research"
    project.mkdir()

    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=no_program)

    assert Path(runtime["python"]).resolve() == Path(sys.executable).resolve()


def test_compiler_settings_are_reused_even_when_the_tools_are_not_on_expandrg_path(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    compilers = {
        "FC": executable(tmp_path / "Installed Toolchain" / "fortran-custom.exe"),
        "CC": executable(tmp_path / "Installed Toolchain" / "c-custom.exe"),
        "CXX": executable(tmp_path / "Installed Toolchain" / "cpp-custom.exe"),
    }
    runtime = discover_runtime(project, environ={"PATH": "", **{key: str(path) for key, path in compilers.items()}},
                               find_program=no_program)
    work = tmp_path / "work"
    work.mkdir()

    for alias, setting in (("{fortran}", "FC"), ("{cc}", "CC"), ("{cxx}", "CXX")):
        command = resolve_runtime_command([alias, "solver.f90", "-o", "solver"], work, work, runtime,
                                          find_program=no_program)
        assert Path(command[0]).resolve() == compilers[setting]
        assert command[1:] == ["solver.f90", "-o", "solver"]


def test_configured_fortran_compiler_preserves_its_flags_in_direct_commands(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    compiler = executable(tmp_path / "Installed Toolchain" / "fortran-custom.exe")
    runtime = discover_runtime(project, environ={"PATH": "", "FC": f'"{compiler}" -O2 -fopenmp'},
                               find_program=no_program)

    command = resolve_runtime_command(["{fortran}", "solver.f90", "-o", "solver"], project, project, runtime,
                                      find_program=no_program)

    assert Path(command[0]).resolve() == compiler
    assert command[1:] == ["-O2", "-fopenmp", "solver.f90", "-o", "solver"]


def test_embedded_make_compiler_alias_preserves_path_quoting_and_flags(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    compiler = executable(tmp_path / "Installed Toolchain" / "fortran-custom.exe")
    make = executable(tmp_path / "build-tools" / "make.exe")
    runtime = discover_runtime(project, environ={"PATH": "", "FC": f'"{compiler}" -O2 -fopenmp'},
                               find_program=lambda name, path=None: str(make) if name == "make" else None)

    command = resolve_runtime_command(["make", "FC={fortran}", "all"], project, project, runtime,
                                      find_program=no_program)

    words = [str(compiler), "-O2", "-fopenmp"]
    assignment = subprocess.list2cmdline(words) if os.name == "nt" else shlex.join(words)
    assert command == [str(make), f"FC={assignment}", "all"]


def test_existing_makefile_compilers_outside_path_are_reused(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    compilers = {
        "FC": executable(tmp_path / "Installed Toolchain" / "custom-fortran.exe"),
        "CC": executable(tmp_path / "Installed Toolchain" / "custom-c.exe"),
        "CXX": executable(tmp_path / "Installed Toolchain" / "custom-cpp.exe"),
    }
    (project / "Makefile").write_text("\n".join([
        f'FC = "{compilers["FC"]}" -O2',
        f'CC := "{compilers["CC"]}" -Wall',
        f'CXX ?= "{compilers["CXX"]}" -std=c++17',
        "all:\n\t$(FC) solver.f90 -o solver",
    ]))

    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=no_program)

    for alias, key, flag in (("{fortran}", "FC", "-O2"), ("{cc}", "CC", "-Wall"),
                             ("{cxx}", "CXX", "-std=c++17")):
        command = resolve_runtime_command([alias, "source"], project, project, runtime,
                                          find_program=no_program)
        assert Path(command[0]).resolve() == compilers[key]
        assert command[1:] == [flag, "source"]


def test_makefile_comment_is_not_part_of_the_compiler(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    compiler = executable(tmp_path / "toolchain" / "gfortran.exe")
    (project / "makefile").write_text(f'FC = "{compiler}"   # compiler\n')

    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=no_program)
    command = resolve_runtime_command(["{fortran}", "solver.f90"], project, project, runtime,
                                      find_program=no_program)

    assert Path(command[0]).resolve() == compiler
    assert command[1:] == ["solver.f90"]


def test_relative_makefile_compiler_uses_project_root_when_connector_cwd_differs(tmp_path, monkeypatch):
    project = tmp_path / "research"
    project.mkdir()
    compiler = executable(project / "toolchain" / "bin" / "gfortran.exe")
    (project / "Makefile").write_text("FC = ./toolchain/bin/gfortran.exe -O2\n")
    connector = tmp_path / "connector"
    connector.mkdir()
    monkeypatch.chdir(connector)
    work = tmp_path / "saved-version" / "work"
    work.mkdir(parents=True)

    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=no_program)
    command = resolve_runtime_command(["{fortran}", "solver.f90"], work, work, runtime,
                                      find_program=no_program)

    assert Path(command[0]).resolve() == compiler
    assert command[1:] == ["-O2", "solver.f90"]


def test_explicit_existing_compiler_is_available_through_a_local_alias(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    compiler = executable(tmp_path / "External compiler" / "gfortran.exe")
    runtime = discover_runtime(project, compiler=str(compiler), environ={"PATH": ""}, find_program=no_program)
    plan = {"build_commands": [["{fortran}", "solver.f90", "-o", "solver.exe"]],
            "run_command": ["./solver.exe"]}

    assert missing_runtime_tools(plan, runtime, find_program=no_program) == []
    work = tmp_path / "work"
    work.mkdir()
    command = resolve_runtime_command(plan["build_commands"][0], work, work, runtime, find_program=no_program)
    assert Path(command[0]).resolve() == compiler


def test_project_environment_bin_directory_is_searched_for_existing_build_tools(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    prefix = project / ".venv"
    python = environment_python(prefix)
    compiler_dir = python.parent
    compiler = executable(compiler_dir / ("gfortran.exe" if os.name == "nt" else "gfortran"))
    looked_up_paths = []
    def find_program(name, path=None):
        looked_up_paths.append(path)
        if name in ("gfortran", "gfortran.exe") and str(compiler_dir) in (path or ""):
            return str(compiler)
        return None

    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=find_program)
    plan = {"build_commands": [["gfortran", "solver.f90", "-o", "solver"]], "run_command": ["./solver"]}

    assert missing_runtime_tools(plan, runtime, find_program=find_program) == []
    assert any(str(compiler_dir) in (path or "") for path in looked_up_paths)


def test_unavailable_tool_is_reported_without_treating_project_outputs_as_missing_tools(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=no_program)
    plan = {"build_commands": [["gfortran", "solver.f90", "-o", "solver"]], "run_command": ["./solver"]}

    assert missing_runtime_tools(plan, runtime, find_program=no_program) == ["gfortran"]


@pytest.mark.parametrize("program,compiler_argument", [
    ("make", "FC={fortran}"),
    ("cmake", "-DCMAKE_Fortran_COMPILER={fortran}"),
])
def test_embedded_compiler_alias_is_checked_before_running(tmp_path, program, compiler_argument):
    project = tmp_path / "research"
    project.mkdir()
    build_tool = executable(tmp_path / "build-tools" / f"{program}.exe")
    runtime = discover_runtime(project, environ={"PATH": ""},
                               find_program=lambda name, path=None: str(build_tool) if name == program else None)
    plan = {"build_commands": [[program, compiler_argument]], "run_command": ["{python}", "solver.py"]}

    assert missing_runtime_tools(plan, runtime, find_program=no_program) == ["{fortran}"]


@pytest.mark.parametrize("command_name", ["{fortran}", "gfortran"])
def test_deleted_registered_compiler_is_unavailable_before_running(tmp_path, command_name):
    project = tmp_path / "research"
    project.mkdir()
    compiler = executable(tmp_path / "Installed Toolchain" / "gfortran.exe")
    runtime = discover_runtime(project, compiler=str(compiler), environ={"PATH": ""}, find_program=no_program)
    compiler.unlink()
    plan = {"build_commands": [[command_name, "solver.f90", "-o", "solver"]], "run_command": ["./solver"]}

    assert missing_runtime_tools(plan, runtime, find_program=no_program) == [command_name]


def test_compiled_project_executable_resolves_inside_the_saved_work_copy(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    work = tmp_path / "saved-version" / "work"
    program = executable(work / "build" / "solver.exe")
    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=no_program)

    command = resolve_runtime_command(["./build/solver.exe", "input.json"], work, work, runtime,
                                      find_program=no_program)

    assert Path(command[0]).resolve() == program
    assert command[1:] == ["input.json"]


def test_runtime_configuration_does_not_save_credentials_from_the_launcher_environment(tmp_path):
    project = tmp_path / "research"
    project.mkdir()
    private = "capi_do_not_forward_environment_secret"
    runtime = discover_runtime(project, environ={"PATH": "", "CREAO_HUB_KEY": private,
                                                "AWS_SECRET_ACCESS_KEY": private, "DATABASE_PASSWORD": private},
                               find_program=no_program)

    assert private not in json.dumps(runtime)


def test_metadata_is_checked_with_the_selected_python_and_keeps_local_paths_private(tmp_path, monkeypatch):
    project = tmp_path / "research"
    project.mkdir()
    python = environment_python(project / ".venv")
    compiler = executable(tmp_path / "External compiler" / "gfortran.exe")
    runtime = discover_runtime(project, compiler=str(compiler), environ={"PATH": ""}, find_program=no_program)
    calls = []
    monkeypatch.setenv("CREAO_HUB_KEY", "capi_do_not_forward_probe_secret")
    def probe(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "python_version": "3.12.7", "python_packages": {"numpy": True, "scipy": False, "unexpected": True},
            "credential": "capi_do_not_forward_probe_secret",
        }))
    monkeypatch.setattr("local_connector.runtime_environment.subprocess.run", probe)

    facts = runtime_facts(runtime)

    assert Path(calls[0][0][0]).resolve() == python
    assert calls[0][0][1:3] == ["-I", "-c"]
    assert "CREAO_HUB_KEY" not in calls[0][1]["env"]
    assert facts["python_version"] == "3.12.7"
    assert facts["python_packages"] == {"numpy": True, "scipy": False}
    assert facts["preferred_compilers"]["Fortran"] == "{fortran}"
    assert facts["reuse_existing_environment"] is True
    assert facts["environment_probe"] == "checked"
    serialized = json.dumps(facts)
    assert str(tmp_path) not in serialized and "capi_do_not_forward_probe_secret" not in serialized


def test_unavailable_metadata_probe_means_unknown_dependencies_not_missing_packages(tmp_path, monkeypatch):
    project = tmp_path / "research"
    project.mkdir()
    runtime = discover_runtime(project, environ={"PATH": ""}, find_program=no_program)
    def timed_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr("local_connector.runtime_environment.subprocess.run", timed_out)

    facts = runtime_facts(runtime)

    assert facts["environment_probe"] == "unknown"
    assert facts["python_version"] == "unknown"
    assert facts["python_packages"] == {}
