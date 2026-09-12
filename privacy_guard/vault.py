"""Local vault: all record/document/checkpoint payloads are authenticated ciphertext.

The portable prototype derives its key from an unlock passphrase. Losing that
passphrase loses access; no recovery secret is stored next to the database.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class Vault:
    MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.data_dir / "vault.sqlite3"
        self._key: bytearray | None = None
        self._mutex = threading.RLock()
        with self._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, payload BLOB NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload BLOB NOT NULL)"
            )
        os.chmod(self.path, 0o600)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def initialized(self) -> bool:
        with self._connection() as db:
            return db.execute("SELECT 1 FROM metadata WHERE name='keycheck'").fetchone() is not None

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    @staticmethod
    def _derive(passphrase: str, salt: bytes) -> bytes:
        if not isinstance(passphrase, str) or len(passphrase) < 10:
            raise ValueError("Use an unlock passphrase with at least 10 characters.")
        if len(passphrase) > 4096:
            raise ValueError("Unlock passphrase is too long.")
        return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))

    def initialize(self, passphrase: str) -> None:
        with self._mutex:
            if self.initialized:
                raise ValueError("The vault has already been initialized.")
            salt = os.urandom(16)
            key = self._derive(passphrase, salt)
            nonce = os.urandom(12)
            check = nonce + AESGCM(key).encrypt(nonce, b"privacy-guard-vault-v1", b"keycheck-v1")
            with self._connection() as db:
                db.execute("INSERT INTO metadata VALUES (?, ?)", ("salt", salt))
                db.execute("INSERT INTO metadata VALUES (?, ?)", ("keycheck", check))
            self._key = bytearray(key)

    def unlock(self, passphrase: str) -> None:
        with self._mutex:
            self.lock()
            with self._connection() as db:
                entries = dict(db.execute("SELECT name, payload FROM metadata"))
            if "salt" not in entries or "keycheck" not in entries:
                raise ValueError("Initialize the vault before unlocking it.")
            key = self._derive(passphrase, entries["salt"])
            payload = entries["keycheck"]
            try:
                value = AESGCM(key).decrypt(payload[:12], payload[12:], b"keycheck-v1")
                if value != b"privacy-guard-vault-v1":
                    raise ValueError("Invalid vault key check.")
            except (InvalidTag, ValueError) as exc:
                raise ValueError("Incorrect passphrase or damaged vault.") from exc
            self._key = bytearray(key)

    def lock(self) -> None:
        with self._mutex:
            if self._key is not None:
                for index in range(len(self._key)):
                    self._key[index] = 0
            self._key = None

    def _require_key(self) -> bytes:
        if self._key is None:
            raise PermissionError("Unlock the local vault to continue.")
        return bytes(self._key)

    def _encode(self, item_id: str, kind: str, value: Any) -> bytes:
        nonce = os.urandom(12)
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return nonce + AESGCM(self._require_key()).encrypt(nonce, data, f"v1:{kind}:{item_id}".encode())

    def _decode(self, item_id: str, kind: str, payload: bytes) -> Any:
        try:
            raw = AESGCM(self._require_key()).decrypt(
                payload[:12], payload[12:], f"v1:{kind}:{item_id}".encode()
            )
            return json.loads(raw)
        except (InvalidTag, json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("Vault entry failed its integrity check.") from exc

    def _list(self, kind: str) -> list[dict]:
        with self._mutex:
            self._require_key()
            with self._connection() as db:
                entries = db.execute(
                    "SELECT id, payload FROM items WHERE kind=? ORDER BY rowid", (kind,)
                ).fetchall()
            return [self._decode(item_id, kind, payload) for item_id, payload in entries]

    def records(self) -> list[dict]:
        return self._list("record")

    def put_record(
        self,
        label: str,
        field_type: str,
        value: str,
        source: str = "manual",
        scope: str = "profile",
        record_id: str | None = None,
        reviewed: bool = True,
    ) -> dict:
        for name, item in (("label", label), ("field_type", field_type), ("value", value), ("scope", scope)):
            if not isinstance(item, str) or not item.strip() or len(item) > 20_000:
                raise ValueError(f"Record {name} must be nonempty text of at most 20,000 characters.")
        with self._mutex:
            self._require_key()
            with self._connection() as db:
                previous = None
                if record_id is not None:
                    row = db.execute(
                        "SELECT payload FROM items WHERE id=? AND kind='record'", (record_id,)
                    ).fetchone()
                    if row is None:
                        raise KeyError("Record does not exist.")
                    previous = self._decode(record_id, "record", row[0])
                item_id = record_id or "rec_" + uuid4().hex
                record = {
                    "id": item_id,
                    "label": label.strip(),
                    "field_type": field_type.strip(),
                    "value": value,
                    "version": previous["version"] + 1 if previous else 1,
                    "source": source,
                    "scope": scope,
                    "reviewed": bool(reviewed),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                payload = self._encode(item_id, "record", record)
                db.execute(
                    "INSERT INTO items VALUES (?, 'record', ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (item_id, payload),
                )
            return record

    def _delete(self, item_id: str, kind: str) -> None:
        with self._mutex:
            self._require_key()
            with self._connection() as db:
                if db.execute("DELETE FROM items WHERE id=? AND kind=?", (item_id, kind)).rowcount == 0:
                    raise KeyError("Vault entry does not exist.")

    def delete_record(self, record_id: str) -> None:
        self._delete(record_id, "record")

    def secrets(self) -> list[str]:
        return sorted({item["value"] for item in self.records() if item["value"]}, key=len, reverse=True)

    def put_document(self, filename: str, data: bytes) -> dict:
        if not isinstance(data, bytes) or not data or len(data) > self.MAX_DOCUMENT_BYTES:
            raise ValueError("Documents must contain between 1 byte and 10 MiB.")
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
            raise ValueError("Provide a document filename of at most 255 characters.")
        with self._mutex:
            self._require_key()
            item_id = "doc_" + uuid4().hex
            record = {
                "id": item_id,
                "filename": Path(filename.replace("\\", "/")).name,
                "size": len(data),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            payload = self._encode(
                item_id, "document", {**record, "data": base64.b64encode(data).decode("ascii")}
            )
            with self._connection() as db:
                db.execute("INSERT INTO items VALUES (?, 'document', ?)", (item_id, payload))
            return record

    def documents(self) -> list[dict]:
        return [{k: v for k, v in record.items() if k != "data"} for record in self._list("document")]

    def get_document(self, document_id: str) -> tuple[str, bytes]:
        with self._mutex:
            self._require_key()
            with self._connection() as db:
                row = db.execute(
                    "SELECT payload FROM items WHERE id=? AND kind='document'", (document_id,)
                ).fetchone()
            if row is None:
                raise KeyError("Document does not exist.")
            record = self._decode(document_id, "document", row[0])
            return record["filename"], base64.b64decode(record["data"], validate=True)

    def delete_document(self, document_id: str) -> None:
        self._delete(document_id, "document")

    def confirm_document(self, document_id: str, candidate_dicts: list[dict]) -> list[dict]:
        """Atomically confirm selected facts and their document's review checkpoint."""
        if not isinstance(candidate_dicts, list) or len(candidate_dicts) > 100:
            raise ValueError("Review at most 100 candidate fields at a time.")
        prepared = []
        for candidate in candidate_dicts:
            if not isinstance(candidate, dict):
                raise ValueError("Document candidates must be objects.")
            if not candidate.get("selected", True):
                continue
            scope = candidate.get("scope", "document")
            if scope == "document":
                scope = "document:" + document_id
            values = {
                "label": candidate.get("label"),
                "field_type": candidate.get("field_type"),
                "value": candidate.get("value"),
                "scope": scope,
            }
            for name, value in values.items():
                if not isinstance(value, str) or not value.strip() or len(value) > 20_000:
                    raise ValueError(f"Record {name} must be nonempty text of at most 20,000 characters.")
            prepared.append(values)
        with self._mutex:
            self._require_key()
            checkpoint_id = "blob:document_" + document_id
            with self._connection() as db:
                # Serialize concurrent review attempts before checking reviewed state.
                db.execute("BEGIN IMMEDIATE")
                original = db.execute(
                    "SELECT payload FROM items WHERE id=? AND kind='document'", (document_id,)
                ).fetchone()
                if original is None:
                    raise KeyError("Document does not exist.")
                self._decode(document_id, "document", original[0])
                row = db.execute(
                    "SELECT payload FROM items WHERE id=? AND kind='blob'", (checkpoint_id,)
                ).fetchone()
                analysis = {} if row is None else self._decode(checkpoint_id, "blob", row[0])
                if analysis.get("reviewed"):
                    raise ValueError(
                        "This document has already been reviewed; edit its saved records in Profile."
                    )
                created = []
                for values in prepared:
                    record = {
                        **values,
                        "id": "rec_" + uuid4().hex,
                        "label": values["label"].strip(),
                        "field_type": values["field_type"].strip(),
                        "version": 1,
                        "reviewed": True,
                        "source": "document:" + document_id,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    payload = self._encode(record["id"], "record", record)
                    db.execute("INSERT INTO items VALUES (?, 'record', ?)", (record["id"], payload))
                    created.append(record)
                analysis["reviewed"] = True
                analysis["record_ids"] = [record["id"] for record in created]
                payload = self._encode(checkpoint_id, "blob", analysis)
                db.execute(
                    "INSERT INTO items VALUES (?, 'blob', ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (checkpoint_id, payload),
                )
            return created

    def save_blob(self, name: str, value: Any) -> None:
        if not isinstance(name, str) or not name or len(name) > 200:
            raise ValueError("Provide a checkpoint identifier of at most 200 characters.")
        with self._mutex:
            item_id = "blob:" + name
            payload = self._encode(item_id, "blob", value)
            with self._connection() as db:
                db.execute(
                    "INSERT INTO items VALUES (?, 'blob', ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (item_id, payload),
                )

    def load_blob(self, name: str, default: Any = None) -> Any:
        with self._mutex:
            self._require_key()
            item_id = "blob:" + name
            with self._connection() as db:
                row = db.execute(
                    "SELECT payload FROM items WHERE id=? AND kind='blob'", (item_id,)
                ).fetchone()
            return default if row is None else self._decode(item_id, "blob", row[0])
