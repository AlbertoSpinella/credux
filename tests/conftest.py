import pytest


@pytest.fixture
def credux_home(tmp_path, monkeypatch):
    """Point CREDUX_HOME at a throwaway directory for every test."""
    home = tmp_path / "credux-home"
    home.mkdir()
    monkeypatch.setenv("CREDUX_HOME", str(home))
    return home
