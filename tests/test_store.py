import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import NoKeyringError, PasswordDeleteError

from credux import store
from credux.errors import SecretStoreError


class MemoryKeyring(KeyringBackend):
    """In-memory backend so tests never touch the real OS keychain."""

    priority = 1

    def __init__(self):
        self.data = {}

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def get_password(self, service, username):
        return self.data.get((service, username))

    def delete_password(self, service, username):
        self.data.pop((service, username), None)


class BrokenKeyring(MemoryKeyring):
    def set_password(self, service, username, password):
        raise NoKeyringError("no backend")

    def get_password(self, service, username):
        raise NoKeyringError("no backend")

    def delete_password(self, service, username):
        raise NoKeyringError("no backend")


class StrictDeleteKeyring(MemoryKeyring):
    """Like a real backend: deleting an absent key raises PasswordDeleteError."""

    def delete_password(self, service, username):
        if (service, username) not in self.data:
            raise PasswordDeleteError("not found")
        super().delete_password(service, username)


@pytest.fixture
def memory_keyring(monkeypatch):
    backend = MemoryKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


def test_set_and_get_round_trip(memory_keyring):
    store.set_secret("cred-1", "aws_access_key_id", "AKIAEXAMPLE")

    assert store.get_secret("cred-1", "aws_access_key_id") == "AKIAEXAMPLE"


def test_key_is_namespaced_by_owner_and_field(memory_keyring):
    store.set_secret("cred-1", "secret", "value")

    assert memory_keyring.data == {("credux", "cred-1:secret"): "value"}


def test_get_returns_none_when_absent(memory_keyring):
    assert store.get_secret("cred-1", "absent") is None


def test_delete_owner_removes_every_listed_field(memory_keyring):
    store.set_secret("cred-1", "a", "1")
    store.set_secret("cred-1", "b", "2")
    store.set_secret("cred-2", "a", "3")

    store.delete_owner("cred-1", ["a", "b"])

    assert store.get_secret("cred-1", "a") is None
    assert store.get_secret("cred-2", "a") == "3"


def test_missing_backend_raises_secret_store_error(monkeypatch):
    backend = BrokenKeyring()
    monkeypatch.setattr(keyring, "set_password", backend.set_password)

    with pytest.raises(SecretStoreError) as excinfo:
        store.set_secret("cred-1", "a", "1")

    assert excinfo.value.exit_code == 5
    assert "keychain" in str(excinfo.value).lower()


def test_delete_secret_absent_is_a_noop(monkeypatch):
    backend = StrictDeleteKeyring()
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)

    store.delete_secret("cred-1", "absent")  # must not raise


def test_backend_name_reports_the_module_as_well_as_the_class(memory_keyring):
    assert store.backend_name() == f"{MemoryKeyring.__module__}.MemoryKeyring"


def test_backend_name_distinguishes_keyrings_no_backend_class(monkeypatch):
    """keyring's "there is no keychain here" backends are both called
    `Keyring`; only their module says so. Reporting the class name alone left
    `credux doctor` unable to tell a headless box from a working one, which is
    exactly the case the README promises it reports."""
    from keyring.backends import fail

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())

    assert store.backend_name() == "keyring.backends.fail.Keyring"


def test_delete_secret_missing_backend_raises_secret_store_error(monkeypatch):
    backend = BrokenKeyring()
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)

    with pytest.raises(SecretStoreError) as excinfo:
        store.delete_secret("cred-1", "a")

    assert excinfo.value.exit_code == 5
    assert "keychain" in str(excinfo.value).lower()
