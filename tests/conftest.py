import pytest


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Keep every test away from the real databases and folder grants."""
    monkeypatch.setenv("EXPANDRG_DATA_DIR", str(tmp_path / "expandrg_data"))
    monkeypatch.setenv("EXPANDRG_HOME", str(tmp_path / "expandrg_home"))
