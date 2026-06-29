"""At-rest encryption (opt-in, [crypto] extra — SQLCipher). I2/I17/D16/D25.

Skips unless the prebuilt SQLCipher wheel (sqlcipher3-binary) is installed — so these run in CI on
Linux x86_64 (where the wheel exists) but skip elsewhere. Default (no key) is plaintext and
unchanged; with a key the whole .db + WAL + snapshots are encrypted (no plaintext on disk).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sqlcipher3") is None,
    reason="needs the [crypto] extra (sqlcipher3-binary)",
)

from cold_frame.api import Memory  # noqa: E402

_NEEDLE = "CONFIDENTIAL-acme-merger-codeword-2026"
_KEY = "correct-horse-battery-staple"


def _disk_bytes(db: str) -> bytes:
    data = Path(db).read_bytes()
    wal = Path(db + "-wal")
    return data + (wal.read_bytes() if wal.exists() else b"")


def test_encrypted_db_has_no_plaintext_on_disk(tmp_path: Path) -> None:
    db = str(tmp_path / "enc.db")
    m = Memory(db, encryption_key=_KEY)
    m.add(f"my secret project is {_NEEDLE}")
    assert any(_NEEDLE in n.content for n in m.list_active())  # readable in-process
    assert m.health()["encrypted"] is True
    m.close()
    assert _NEEDLE.encode() not in _disk_bytes(db)  # ...but NOT on disk (encrypted at rest)
    header = Path(db).read_bytes()[:16]
    assert not header.startswith(b"SQLite format 3")  # SQLCipher → no plaintext SQLite header


def test_wrong_key_cannot_open_right_key_can(tmp_path: Path) -> None:
    db = str(tmp_path / "enc.db")
    Memory(db, encryption_key=_KEY).add(f"fact {_NEEDLE}")
    with pytest.raises(Exception):  # noqa: B017 - SQLCipher rejects a wrong key on first access
        Memory(db, encryption_key="totally-wrong-key").list_active()
    again = Memory(db, encryption_key=_KEY)
    assert any(_NEEDLE in n.content for n in again.list_active())  # right key → readable


def test_default_path_is_plaintext_and_unchanged(tmp_path: Path) -> None:
    # the zero-config default (no key) stays plaintext sqlite3 — encryption never touches it
    db = str(tmp_path / "plain.db")
    m = Memory(db)
    m.add(f"plain note {_NEEDLE}")
    assert m.health()["encrypted"] is False
    m.close()
    assert _NEEDLE.encode() in _disk_bytes(db)


def test_snapshot_of_encrypted_db_stays_encrypted(tmp_path: Path) -> None:
    # the leak vector: a snapshot/backup must NOT be written in plaintext (it's keyed too)
    db = str(tmp_path / "enc.db")
    m = Memory(db, encryption_key=_KEY)
    m.add(f"snapshot secret {_NEEDLE}")
    snap = str(tmp_path / "backup.db")
    m.snapshot(snap)
    assert _NEEDLE.encode() not in _disk_bytes(snap)  # backup is encrypted, no plaintext leak
    restored = Memory(snap, encryption_key=_KEY)  # ...and restorable with the key
    assert any(_NEEDLE in n.content for n in restored.list_active())
