import pytest


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Keep every test away from the real databases and folder grants."""
    monkeypatch.setenv("SIMGUARD_DATA_DIR", str(tmp_path / "simguard_data"))
    monkeypatch.setenv("SIMGUARD_HOME", str(tmp_path / "simguard_home"))
