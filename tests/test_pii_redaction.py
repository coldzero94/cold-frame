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


def test_redact_scrubs_content_context_and_keywords_and_rehashes_source(db_path: str) -> None:
    # PII must be scrubbed from ALL persisted grains (content/context/keywords — keywords are
    # FTS-indexed), and the source content_hash re-derived so no SHA of the original PII lingers.
    from datetime import UTC, datetime

    from cold_frame.models import Note, Scope, Source

    t = datetime(2026, 1, 1, tzinfo=UTC)
    note = Note(
        id="n",
        content="my email is alice@corp.com",
        context="cc bob@corp.com",
        keywords=["carol@corp.com", "vim"],
        memory_type="semantic",
        scope=Scope(),
        created_at=t,
        sources=[Source(kind="message", ref="m", content_hash="ORIGINAL_HASH", observed_at=t)],
    )
    wc = Memory(db_path, pii_redact=frozenset({"email"}))._write
    scrubbed, summ = wc._redact(note)
    assert "@corp.com" not in scrubbed.content and "@corp.com" not in scrubbed.context
    assert all("@corp.com" not in k for k in scrubbed.keywords)  # keyword PII scrubbed too
    assert summ["email"] == 3  # content + context + 1 keyword
    assert scrubbed.sources[0].content_hash != "ORIGINAL_HASH"  # re-hashed over redacted content


def test_correct_memory_redacts_pii_on_the_supersede_path(db_path: str) -> None:
    m = Memory(db_path, pii_redact=frozenset({"email"}))
    nid = m.add("I deploy this repo").added[0].id
    m.correct_memory(nid, "actually email me at dave@corp.com from now on")
    assert all("dave@corp.com" not in n.content for n in m.list_active())  # redacted in-place
    assert b"dave@corp.com" not in _db_bytes(db_path)  # and absent from disk
