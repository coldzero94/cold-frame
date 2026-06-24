"""Round-trip proof: the server's ACTUAL emitted JSON conforms to the contract that generates the
TS client — so "the TS types describe the real responses" is proven, not assumed.

This closes the loop mypy cannot: mypy pins each builder's STATIC return type to its TypedDict, but
the builders in server.py assemble dicts BY HAND, so a statically-Band-typed value that is out of
union at runtime would ship with every static guard green. Validating the real payloads against the
contract TypedDicts here (runtime values, no Node) is the missing link.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cold_frame.api import Memory
from cold_frame.models import Edge
from cold_frame.ui import server as ui
from cold_frame.ui.contract import FactDetailDict, MemoryFieldResponse, NotesResponse
from pydantic import TypeAdapter, ValidationError


def _seed(memory: Memory) -> str:
    """A varied corpus: a healthy note, a forced fading+at-risk note, and a fact with a source+edge,
    so validation exercises non-default band/at_risk values, not just the fresh-note path."""
    a = memory.add("I prefer dark roast coffee").added[0].id
    b = memory.add("an old, low-confidence, decayed memory").added[0].id
    now = memory._clock.now()
    memory._store._conn.execute(  # paint b into the fading + at-risk corner (per gen_sample.py)
        "UPDATE notes SET importance=?, decay_S=?, last_accessed=?, confidence=? WHERE id=?",
        (0.1, 5.0, (now - timedelta(days=120)).isoformat(), 0.2, b),
    )
    memory._store._conn.commit()
    memory._store.add_edge(Edge(src_id=a, dst_id=b, relation="relates_to", created_at=now))
    return a


def test_payloads_validate_against_contract_types(memory: Memory) -> None:
    fid = _seed(memory)
    # strict=True → no lax coercion: a stringified number or out-of-union literal would be rejected
    TypeAdapter(NotesResponse).validate_python(ui.notes_payload(memory), strict=True)
    mf = ui.memory_field_payload(memory)
    assert {n["band"] for n in mf["notes"]} & {"fading"}  # the forced fading note really shows up
    assert any(n["atRisk"] for n in mf["notes"])
    TypeAdapter(MemoryFieldResponse).validate_python(mf, strict=True)
    fact = ui.fact_payload(memory, fid)
    assert fact is not None and fact["sources"] and fact["edges"]  # provenance present
    TypeAdapter(FactDetailDict).validate_python(fact, strict=True)


def test_roundtrip_guard_has_teeth(memory: Memory, monkeypatch: pytest.MonkeyPatch) -> None:
    memory.add("a memory")
    # a builder emitting an out-of-union band MUST be rejected — proves the guard is not a no-op
    bad = {
        "id": "1", "content": "x", "memory_type": "semantic", "status": "active",
        "confidence": 1.0, "strength": {"value": 0.5, "band": "WRONG", "at_risk": False},
    }
    monkeypatch.setattr(ui, "_note_brief", lambda *a, **k: bad)
    with pytest.raises(ValidationError):
        TypeAdapter(NotesResponse).validate_python(ui.notes_payload(memory), strict=True)
