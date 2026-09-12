import sqlite3

import pytest

from privacy_guard.vault import Vault


@pytest.fixture
def vault(tmp_path):
    instance = Vault(tmp_path / "vault")
    instance.initialize("correct horse battery staple")
    return instance


def test_initialized_locked_and_wrong_password(tmp_path):
    vault = Vault(tmp_path / "vault")
    assert not vault.initialized and not vault.unlocked
    with pytest.raises(ValueError):
        vault.initialize("short")
    assert not vault.initialized
    vault.initialize("correct horse battery staple")
    assert vault.initialized and vault.unlocked
    vault.lock()
    with pytest.raises(PermissionError):
        vault.records()
    with pytest.raises(ValueError):
        vault.unlock("incorrect horse password")
    assert not vault.unlocked
    vault.unlock("correct horse battery staple")
    assert vault.unlocked
    with pytest.raises(ValueError):
        vault.initialize("another long password")


def test_values_filenames_and_checkpoints_are_encrypted_at_rest(vault):
    canary = "SYNTHETIC-PRIVATE-SENTINEL-492761"
    record = vault.put_record("Private sentinel label", "full_name", canary)
    document = vault.put_document("private-filename-canary.txt", canary.encode())
    vault.save_blob("task_1", {"answer": canary})
    assert vault.get_document(document["id"]) == ("private-filename-canary.txt", canary.encode())
    assert vault.load_blob("task_1")["answer"] == canary
    for path in vault.data_dir.iterdir():
        if path.is_file():
            raw = path.read_bytes()
            assert canary.encode() not in raw
            assert b"Private sentinel label" not in raw
            assert b"private-filename-canary" not in raw
    vault.lock()
    second = Vault(vault.data_dir)
    assert second.initialized and not second.unlocked
    second.unlock("correct horse battery staple")
    assert second.records()[0]["id"] == record["id"]
    assert second.secrets() == [canary]


def test_update_requires_existing_id_and_tracks_version_scope(vault):
    first = vault.put_record(
        "Address", "address", "Old street", source="document:1", scope="document", reviewed=False
    )
    assert first["version"] == 1 and not first["reviewed"]
    second = vault.put_record("Address", "address", "New street", record_id=first["id"])
    assert second["id"] == first["id"] and second["version"] == 2
    assert len(vault.records()) == 1
    assert second["scope"] == "profile" and second["reviewed"]
    with pytest.raises(KeyError):
        vault.put_record("Address", "address", "New street", record_id="missing")
    vault.delete_record(first["id"])
    assert not vault.records()


def test_ciphertext_tampering_and_row_swapping_are_detected(vault):
    first = vault.put_record("Name", "full_name", "Person A")
    second = vault.put_record("Name", "full_name", "Person B")
    with sqlite3.connect(vault.path) as db:
        payload = db.execute("SELECT payload FROM items WHERE id=?", (first["id"],)).fetchone()[0]
        db.execute("UPDATE items SET payload=? WHERE id=?", (payload, second["id"]))
    with pytest.raises(ValueError, match="integrity"):
        vault.records()


def test_documents_are_deleted_and_paths_are_not_reused(vault):
    document = vault.put_document("../../secret.txt", b"sample document")
    assert document["filename"] == "secret.txt"
    assert "data" not in vault.documents()[0]
    vault.delete_document(document["id"])
    assert vault.documents() == []
    with pytest.raises(KeyError):
        vault.get_document(document["id"])


def test_locked_mutations_and_document_reads_fail(vault):
    record = vault.put_record("Name", "full_name", "Example")
    document = vault.put_document("example.txt", b"example")
    vault.lock()
    operations = [
        lambda: vault.secrets(),
        lambda: vault.documents(),
        lambda: vault.get_document(document["id"]),
        lambda: vault.put_record("Name", "full_name", "Other"),
        lambda: vault.delete_record(record["id"]),
        lambda: vault.put_document("a.txt", b"x"),
        lambda: vault.save_blob("task", {}),
        lambda: vault.load_blob("task"),
    ]
    for operation in operations:
        with pytest.raises(PermissionError):
            operation()
