"""PII redaction (opt-in, I6 REDACT) — deterministic inline scrub before persist.

A personal-memory tool must NOT blanket-redact the user's own contact facts, so redaction is
OFF by default; when enabled per-category it removes the PII inline before embed/persist (never on
disk) and reports it content-free in AddResult.redacted.
"""

from __future__ import annotations

from pathlib import Path

from cold_frame.api import Memory
from cold_frame.write.admission import PII_CATEGORIES, redact_pii


def _db_bytes(path: str) -> bytes:
    """Raw on-disk bytes (main + WAL) — proves a value never reached disk in any grain."""
    data = Path(path).read_bytes()
    wal = Path(path + "-wal")
    return data + (wal.read_bytes() if wal.exists() else b"")


def test_redact_pii_unit_scrubs_pii_but_not_ports_or_versions() -> None:
    red, summ = redact_pii("email me at a@b.com or call +1 (415) 555-0199", PII_CATEGORIES)
    assert "a@b.com" not in red and "[email]" in red and "[phone]" in red
    assert summ == {"email": 1, "phone": 1}
    # ssn + card
    red2, summ2 = redact_pii("ssn 123-45-6789, card 4111 1111 1111 1111", PII_CATEGORIES)
    assert "123-45-6789" not in red2 and "4111" not in red2
    assert summ2 == {"ssn": 1, "credit_card": 1}
    # NO false positives on technical numbers
    clean = "Postgres 16 on port 27182, deploy v2.3.1 with 8 workers"
    red3, summ3 = redact_pii(clean, PII_CATEGORIES)
    assert summ3 == {} and red3 == clean


def test_memory_default_keeps_personal_contact_facts(db_path: str) -> None:
    # default OFF: a personal-memory tool should remember "my email is …" verbatim
    res = Memory(db_path).add("my email is jane@corp.com")
    assert "jane@corp.com" in res.added[0].content
    assert res.redacted == []


def test_memory_pii_redaction_opt_in_scrubs_before_disk(db_path: str) -> None:
    m = Memory(db_path, pii_redact=frozenset({"email"}))
    res = m.add("my work email is jane@corp.com and I prefer dark roast coffee")
    note = res.added[0]
    assert "jane@corp.com" not in note.content and "[email]" in note.content
    assert "dark roast coffee" in note.content  # the non-PII fact survives intact
    assert [(r.category, r.count) for r in res.redacted] == [("email", 1)]
    # the address is reachable nowhere in the persisted notes/fts grains (never hit disk)
    assert b"jane@corp.com" not in _db_bytes(db_path)
    assert "jane@corp.com" not in m.get(note.id).content


def test_cli_add_redact_pii_flag(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def,name-defined]
    from cold_frame.cli import main

    db = str(tmp_path / "m.db")
    assert main(["--db", db, "add", "ping me at bob@acme.io anytime", "--redact-pii"]) == 0
    out = capsys.readouterr().out
    assert "bob@acme.io" not in out and "[email]" in out
    assert "redacted 1x email" in out  # content-free count line
    # without the flag, the user's own contact fact is kept verbatim (default off)
    assert main(["--db", db, "add", "ping me at carol@acme.io"]) == 0
    assert "carol@acme.io" in capsys.readouterr().out
