import pytest


@pytest.fixture
def credux_home(tmp_path, monkeypatch):
    """Point CREDUX_HOME at a throwaway directory for every test."""
    home = tmp_path / "credux-home"
    home.mkdir()
    monkeypatch.setenv("CREDUX_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def isolated_aws_environment(tmp_path, monkeypatch):
    """Keep the developer's own AWS environment out of every test.

    A machine that has run `credux env` exports AWS_PROFILE, and any bare
    `boto3.client(...)` reads it back - including the one behind the
    `stubbed_client` fixture. Without this the whole provider suite fails with
    ProfileNotFound on a working machine while passing in a clean CI runner.
    The file variables are pinned for the same reason the filesystem is: no
    test may reach a real ~/.aws.
    """
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
