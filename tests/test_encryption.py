"""At-rest encryption (opt-in, [crypto] extra — SQLCipher). I2/I17/D16/D25.

Skips unless the prebuilt SQLCipher wheel (sqlcipher3-binary) is installed — so these run in CI on
Linux x86_64 (where the wheel exists) but skip elsewhere. Default (no key) is plaintext and
unchanged; with a key the whole .db + WAL + snapshots are encrypted (no plaintext on disk).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from cold_frame.api import Memory
from cold_frame.exceptions import StoreError

# the encrypt/decrypt tests need the SQLCipher wheel (CI Linux); key-resolution tests don't.
_needs_sqlcipher = pytest.mark.skipif(
    importlib.util.find_spec("sqlcipher3") is None,
    reason="needs the [crypto] extra (sqlcipher3-binary)",
)

_NEEDLE = "CONFIDENTIAL-acme-merger-codeword-2026"
_KEY = "correct-horse-battery-staple"


def _disk_bytes(db: str) -> bytes:
    data = Path(db).read_bytes()
    wal = Path(db + "-wal")
    return data + (wal.read_bytes() if wal.exists() else b"")


@_needs_sqlcipher
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


@_needs_sqlcipher
def test_wrong_key_cannot_open_right_key_can(tmp_path: Path) -> None:
    db = str(tmp_path / "enc.db")
    Memory(db, encryption_key=_KEY).add(f"fact {_NEEDLE}")
    with pytest.raises(StoreError):  # typed, key-free error (not a raw "file is not a database")
        Memory(db, encryption_key="totally-wrong-key")
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


@_needs_sqlcipher
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


# ── key resolution (no SQLCipher needed — these run everywhere) ────────────────
def test_blank_key_fails_closed_not_silently_plaintext(tmp_path: Path) -> None:
    # a SET-BUT-BLANK key is a misconfig → must raise, never silently downgrade to plaintext
    with pytest.raises(ValueError, match="blank"):
        Memory(str(tmp_path / "x.db"), encryption_key="")
    with pytest.raises(ValueError, match="blank"):
        Memory(str(tmp_path / "y.db"), encryption_key="   ")


def test_unset_key_is_plaintext_default(tmp_path: Path) -> None:
    # explicit None (and no env) → the zero-config plaintext default, no error
    m = Memory(str(tmp_path / "z.db"), encryption_key=None)
    assert m.health()["encrypted"] is False


@_needs_sqlcipher
def test_purge_under_encryption_verifies_via_keyed_connection(tmp_path: Path) -> None:
    # under encryption the raw file is ciphertext, so grep_clean must be proven via the keyed
    # (decrypting) connection — not a vacuous byte-grep that's always "clean" for the wrong reason.
    db = str(tmp_path / "enc.db")
    m = Memory(db, encryption_key=_KEY)
    nid = m.add(f"purge me {_NEEDLE}").added[0].id
    report = m._store.purge(nid)
    assert report.grep_clean is True  # logical scrub verified through the decrypting connection
    assert all(_NEEDLE not in n.content for n in m.list_active())  # and the secret is gone


@_needs_sqlcipher
def test_migrate_plaintext_to_encrypted(tmp_path: Path) -> None:
    # the create-time-only escape hatch: a plaintext DB → a fully-encrypted copy (notes + FTS +
    # vectors), non-destructive. sqlcipher_export (NOT the raw-page backup API, which can't change
    # encryption) does the re-encrypt.
    from cold_frame.store.sqlite import migrate_to_encrypted

    src = str(tmp_path / "plain.db")
    m = Memory(src)
    m.add(f"my secret project is {_NEEDLE}")
    m.add("the mitochondria is the powerhouse of the cell")
    m.close()

    dst = str(tmp_path / "enc.db")
    migrate_to_encrypted(src, dst, _KEY)

    enc = Memory(dst, encryption_key=_KEY)
    assert enc.health()["encrypted"] is True
    assert any(_NEEDLE in n.content for n in enc.list_active())  # notes copied
    assert enc.search("mitochondria").hits  # the FTS index survived the migration
    enc.close()
    assert _NEEDLE.encode() not in _disk_bytes(dst)  # encrypted at rest — no plaintext
    with pytest.raises(StoreError):  # wrong key cannot open the encrypted copy
        Memory(dst, encryption_key="totally-wrong-key")
    assert _NEEDLE.encode() in Path(src).read_bytes()  # source untouched (non-destructive)


@_needs_sqlcipher
def test_migrate_refuses_blank_key_and_existing_dst(tmp_path: Path) -> None:
    from cold_frame.store.sqlite import migrate_to_encrypted

    src = str(tmp_path / "plain.db")
    Memory(src).add("a fact")
    with pytest.raises(StoreError, match="blank"):  # blank key would fail open — refuse
        migrate_to_encrypted(src, str(tmp_path / "a.db"), "  ")
    existing = tmp_path / "exists.db"
    existing.write_bytes(b"do not clobber")
    with pytest.raises(StoreError, match="already exists"):  # never overwrite the destination
        migrate_to_encrypted(src, str(existing), _KEY)


@_needs_sqlcipher
def test_cli_encrypt_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cold_frame.cli import main

    src = str(tmp_path / "plain.db")
    Memory(src).add(f"cli secret {_NEEDLE}")
    dst = str(tmp_path / "enc.db")
    monkeypatch.setenv("COLD_FRAME_KEY", _KEY)  # key from the env (not echoed to argv)
    assert main(["--db", src, "encrypt", "--out", dst]) == 0
    assert _NEEDLE.encode() not in _disk_bytes(dst)  # the produced copy is encrypted at rest
    restored = Memory(dst, encryption_key=_KEY)
    assert any(_NEEDLE in n.content for n in restored.list_active())


@_needs_sqlcipher
def test_cli_import_restores_an_encrypted_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cold_frame.cli import main

    monkeypatch.setenv("COLD_FRAME_KEY", _KEY)  # the import path resolves the key from the env
    src = str(tmp_path / "enc.db")
    Memory(src, encryption_key=_KEY).add(f"backed up {_NEEDLE}")
    snap = str(tmp_path / "snap.db")
    Memory(src, encryption_key=_KEY).snapshot(snap)  # an ENCRYPTED snapshot
    dst = str(tmp_path / "restored.db")
    assert main(["--db", dst, "import", snap]) == 0  # validates + restores (no crash on ciphertext)
    restored = Memory(dst, encryption_key=_KEY)
    assert any(_NEEDLE in n.content for n in restored.list_active())
