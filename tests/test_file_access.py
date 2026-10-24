"""Tests for folder grants, evidence extraction and the shared evidence rules."""
import os
from pathlib import Path

import pytest

from backend.orchestration.evidence_policy import MAX_ITEM_CHARS, MAX_TOTAL_CHARS, path_problem, redact_secrets
from local_connector.evidence.extract import extract_evidence, parse_selection
from local_connector.file_access.grants import AccessDenied, GrantRegistry

SAMPLE = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture
def project(tmp_path):
    """A copy of the sample project plus decoy secret files (made here, never committed)."""
    root = tmp_path / "heat_project"
    root.mkdir()
    for name in ("heat2d.py", "config.json", "run.log"):
        (root / name).write_text((SAMPLE / name).read_text(encoding="utf-8"), encoding="utf-8")
    (root / ".env").write_text("CREAO_HUB_KEY=capi_decoy_secret_value_123\n")
    (root / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[remote]\nurl = https://token@github.com/x\n")
    (root / "id_rsa").write_text("not really a key\n")
    (root / "results.bin").write_bytes(b"\x00\x01\x02binary")
    return root


@pytest.fixture
def registry(tmp_path):
    return GrantRegistry(state_file=tmp_path / "state" / "grants.json")


# --- Grants ----------------------------------------------------------------


def test_reads_a_file_inside_a_granted_folder(registry, project):
    grant = registry.grant(project)

    relative, text, truncated = registry.read_file("config.json", grant)

    assert relative == "config.json"
    assert '"dt": 0.03' in text
    assert truncated is False


def test_refuses_a_folder_that_was_not_granted(registry, project):
    with pytest.raises(AccessDenied, match="has not been granted"):
        registry.grant_for_root(project)


def test_refuses_files_outside_the_granted_folder(registry, project, tmp_path):
    grant = registry.grant(project)
    outside = tmp_path / "outside.txt"
    outside.write_text("private")

    with pytest.raises(AccessDenied, match="outside the granted"):
        registry.read_file(outside, grant)
    with pytest.raises(AccessDenied, match="outside the granted"):
        registry.read_file("../outside.txt", grant)


@pytest.mark.parametrize("name", [".env", "deploy.pem", ".git/config", "id_rsa"])
def test_refuses_sensitive_files_even_inside_a_granted_folder(registry, project, name):
    grant = registry.grant(project)

    with pytest.raises(AccessDenied, match="sensitive"):
        registry.read_file(name, grant)


def test_refuses_binary_files(registry, project):
    grant = registry.grant(project)

    with pytest.raises(AccessDenied, match="binary"):
        registry.read_file("results.bin", grant)


def test_symlink_out_of_the_folder_is_refused(registry, project, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    try:
        os.symlink(outside, project / "link.txt")
    except OSError:
        pytest.skip("creating symlinks needs extra permissions on this computer")
    grant = registry.grant(project)

    with pytest.raises(AccessDenied, match="outside the granted"):
        registry.read_file("link.txt", grant)


def test_revoked_folder_can_no_longer_be_read(registry, project):
    registry.grant(project)
    assert registry.revoke(project) is True

    with pytest.raises(AccessDenied):
        registry.grant_for_root(project)


def test_grants_are_remembered(registry, project):
    registry.grant(project)

    again = GrantRegistry(state_file=registry.state_file)

    assert again.grant_for_root(project).name == "heat_project"


def test_refuses_to_grant_a_whole_drive_or_home_folder(registry, tmp_path, monkeypatch):
    with pytest.raises(AccessDenied, match="specific project folder"):
        registry.grant(Path(tmp_path.anchor))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(AccessDenied, match="specific project folder"):
        registry.grant(tmp_path)


# --- Evidence extraction ---------------------------------------------------


def test_sample_log_evidence_keeps_the_failure(registry, project):
    evidence = extract_evidence(registry, registry.grant(project), ["run.log"])

    excerpt = evidence.items[0].excerpt
    assert evidence.items[0].kind == "log"
    assert "max|T| = inf" in excerpt
    assert "ERROR: non-finite temperature at step 2223" in excerpt
    assert "heat2d: grid 41x41" in excerpt  # first lines are kept


def test_long_log_keeps_only_head_anomalies_and_tail(registry, project):
    lines = [f"step {i} residual = 1.0e-03" for i in range(1, 3001)]
    lines[1499] = "step 1500 residual = nan"
    (project / "long.log").write_text("\n".join(lines))

    item = extract_evidence(registry, registry.grant(project), ["long.log"]).items[0]

    assert "step 1 residual" in item.excerpt  # head
    assert " 1500| step 1500 residual = nan" in item.excerpt  # anomaly, with its line number
    assert "step 3000 residual" in item.excerpt  # tail
    assert "step 700 residual" not in item.excerpt
    assert item.truncated is True
    assert len(item.excerpt) < 2000


def test_line_range_selection(registry, project):
    item = extract_evidence(registry, registry.grant(project), ["heat2d.py:18-24"]).items[0]

    numbered = [line for line in item.excerpt.splitlines() if "|" in line]
    assert item.kind == "code"
    assert item.excerpt.startswith("  ...")  # marks the lines left out before the range
    assert numbered[0].startswith("   18|") and numbered[-1].startswith("   24|")
    assert "lap = T[i + 1][j]" in item.excerpt
    assert '"""Explicit 2D heat' not in item.excerpt
    assert item.truncated is True


def test_small_file_is_sent_whole(registry, project):
    item = extract_evidence(registry, registry.grant(project), ["config.json"]).items[0]

    assert item.kind == "config"
    assert '"dt": 0.03' in item.excerpt
    assert item.truncated is False


def test_secrets_inside_files_are_blanked(registry, project):
    (project / "notes.txt").write_text("api_key = sk-123456\nCREAO key capi_FAKE_abcdefgh\nall fine\n")

    evidence = extract_evidence(registry, registry.grant(project), ["notes.txt"])

    excerpt = evidence.items[0].excerpt
    assert "sk-123456" not in excerpt
    assert "capi_FAKE_abcdefgh" not in excerpt
    assert "all fine" in excerpt
    assert evidence.redactions == 2


def test_a_sensitive_selection_stops_the_whole_extraction(registry, project):
    with pytest.raises(AccessDenied, match="sensitive"):
        extract_evidence(registry, registry.grant(project), ["run.log", ".env"])


def test_size_limits_are_respected(registry, project):
    names = []
    for k in range(5):
        (project / f"module{k}.py").write_text("\n".join(f"x{k}_{i} = {i}" for i in range(1500)))
        names.append(f"module{k}.py")

    evidence = extract_evidence(registry, registry.grant(project), names)

    assert all(len(i.excerpt) <= MAX_ITEM_CHARS for i in evidence.items)
    assert evidence.total_chars <= MAX_TOTAL_CHARS
    assert evidence.skipped  # some files didn't fit
    assert all(i.truncated for i in evidence.items)


def test_evidence_never_contains_local_paths(registry, project, tmp_path):
    evidence = extract_evidence(registry, registry.grant(project), ["run.log", "heat2d.py:1-10"])

    for item in evidence.items:
        assert path_problem(item.path) is None
        assert str(tmp_path) not in item.excerpt
    assert evidence.project == "heat_project"


def test_parse_selection():
    assert parse_selection("run.log") == ("run.log", None)
    assert parse_selection("solver.py:10-40") == ("solver.py", (10, 40))
    assert parse_selection(r"C:\work\solver.py:10-40") == (r"C:\work\solver.py", (10, 40))
    with pytest.raises(ValueError):
        parse_selection("solver.py:40-10")


# --- Shared evidence rules -------------------------------------------------


@pytest.mark.parametrize("path", ["run.log", "src/solver.py", "configs/a.json", "notes/.envrc.md"])
def test_acceptable_paths(path):
    assert path_problem(path) is None


@pytest.mark.parametrize("path, problem", [
    (r"C:\Users\me\run.log", "relative"),
    ("/home/me/run.log", "relative"),
    ("../run.log", "leave"),
    (".env", "sensitive"),
    (".env.local", "sensitive"),
    ("keys/server.KEY", "sensitive"),
    (".git/config", "sensitive"),
    (".ssh/known_hosts", "sensitive"),
])
def test_rejected_paths(path, problem):
    assert problem in path_problem(path)


def test_redact_secrets():
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n"
        "CREAO_HUB_KEY=capi_FAKE_01234567\n"
        "aws AKIAABCDEFGHIJKLMNOP\n"
        'password: "hunter2"\n'
        "dt = 0.03\n"
    )

    redacted, count = redact_secrets(text)

    for secret in ("MIIE", "capi_FAKE_01234567", "AKIAABCDEFGHIJKLMNOP", "hunter2"):
        assert secret not in redacted
    assert "dt = 0.03" in redacted
    assert count == 4
