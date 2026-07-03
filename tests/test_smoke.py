"""Scaffold smoke tests — all PASSING (the scaffold must stay green; no red tests).

These pin the frozen contract surface so a later refactor that breaks an invariant
fails loudly here: package exports, branding port, constants, ABC abstractness,
the Note model + G2 flag columns, the 3-value Status, the exception hierarchy, and
the deterministic L2-normalized HashEmbedder.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import get_args

import cold_frame
import numpy as np
import pytest
from cold_frame import Memory, Note, Scope, SearchResult, Source, branding, constants
from cold_frame.constants import HASH_EMBED_DIM
from cold_frame.exceptions import ColdFrameError, NoteNotFound, PolicyError
from cold_frame.llm.base import LLM, Embedder, HashEmbedder
from cold_frame.models import StatusLiteral
from cold_frame.store.base import Store


# ── package surface ──────────────────────────────────────────────────────────
def test_package_imports_and_exports() -> None:
    assert cold_frame.__version__ == "0.1.1"
    for name in ("Memory", "Note", "Scope", "Source", "SearchResult", "__version__"):
        assert hasattr(cold_frame, name), name
    # the public symbols resolve to real objects
    assert Memory is cold_frame.Memory
    assert Note is cold_frame.Note
    assert Scope is cold_frame.Scope
    assert Source is cold_frame.Source
    assert SearchResult is cold_frame.SearchResult


# ── branding (the only port lives here; literal 27182 forbidden elsewhere) ───
def test_ui_port_is_frozen() -> None:
    assert branding.UI_PORT == 27182
    assert branding.UI_HOST == "127.0.0.1"
    assert branding.PKG == "cold-frame"
    assert branding.URL_SCHEME == "cold-frame"
    assert branding.resource_uri("abc") == "cold-frame://fact/abc"
    assert branding.fact_deeplink("abc", port=27182).endswith("/fact/abc")


# ── frozen constants exist and are typed ─────────────────────────────────────
def test_key_constants_exist_and_typed() -> None:
    assert pytest.approx(0.45) == constants.W_RETRIEVABILITY
    assert pytest.approx(0.35) == constants.W_IMPORTANCE
    assert pytest.approx(0.20) == constants.W_ACCESS
    assert pytest.approx(0.66) == constants.BAND_EVERGREEN
    assert pytest.approx(0.33) == constants.BAND_BUDDING
    assert pytest.approx(0.20) == constants.ARCHIVE_THRESHOLD
    # per-scope caps (I13)
    assert constants.CAP_SEMANTIC == 2000
    assert constants.CAP_EPISODIC == 500
    assert constants.CAP_PROCEDURAL == 100
    assert constants.HASH_EMBED_DIM == 256
    assert constants.EMBED_METRIC == "cosine"
    assert constants.SCHEMA_VERSION == 1
    # types are concrete (not None / placeholders)
    assert isinstance(constants.W_RETRIEVABILITY, float)
    assert isinstance(constants.CAP_SEMANTIC, int)
    assert isinstance(constants.EMBED_METRIC, str)


# ── ABCs cannot be instantiated (Store / Embedder / LLM are abstract) ────────
@pytest.mark.parametrize("abc_cls", [Store, Embedder, LLM])
def test_abcs_are_abstract(abc_cls: type) -> None:
    with pytest.raises(TypeError):
        abc_cls()


# ── Note model constructs + round-trips ──────────────────────────────────────
def _make_note() -> Note:
    return Note(
        id="n1",
        content="user prefers dark roast coffee",
        memory_type="semantic",
        scope=Scope(),
        created_at=datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC),
        sources=[
            Source(
                kind="message",
                ref="msg-1",
                content_hash="deadbeef",
                observed_at=datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC),
            )
        ],
    )


def test_note_constructs_and_round_trips() -> None:
    note = _make_note()
    dumped = note.model_dump()
    restored = Note.model_validate(dumped)
    assert restored == note
    # JSON round-trip too (tz-aware datetimes survive)
    restored_json = Note.model_validate_json(note.model_dump_json())
    assert restored_json.id == note.id
    assert restored_json.created_at.tzinfo is not None


def test_note_has_g2_flag_columns() -> None:
    note = _make_note()
    # G2: quarantine is a flag column, NOT a 4th Status value
    assert note.held_for_human is False
    assert note.quarantined is False
    assert note.triage_reason is None
    assert "held_for_human" in Note.model_fields
    assert "quarantined" in Note.model_fields
    assert "triage_reason" in Note.model_fields


# ── Status is exactly 3 values (G2) ──────────────────────────────────────────
def test_status_has_exactly_three_values() -> None:
    values = set(get_args(StatusLiteral))
    assert values == {"active", "archived", "deleted"}
    assert len(values) == 3


# ── exception hierarchy ──────────────────────────────────────────────────────
def test_exception_hierarchy() -> None:
    assert issubclass(PolicyError, ColdFrameError)
    assert issubclass(NoteNotFound, ColdFrameError)
    assert isinstance(PolicyError("x"), ColdFrameError)


# ── HashEmbedder: shape (n, 256), deterministic, L2-normalized ───────────────
def test_hash_embedder_shape_deterministic_normalized() -> None:
    emb = HashEmbedder()
    assert emb.meta.dim == HASH_EMBED_DIM
    assert emb.is_local is True

    texts = ["dark roast coffee", "i like green tea", "another sentence here"]
    out = emb.embed(texts)
    assert out.shape == (3, HASH_EMBED_DIM)
    assert out.dtype == np.float32

    # deterministic: same input → identical vectors
    out2 = emb.embed(texts)
    np.testing.assert_array_equal(out, out2)

    # L2-normalized (each non-empty row has unit norm)
    norms = np.linalg.norm(out, axis=1)
    np.testing.assert_allclose(norms, np.ones(3), rtol=0, atol=1e-5)

    # embed_one returns a single (dim,) vector matching the batch row
    one = emb.embed_one(texts[0])
    assert one.shape == (HASH_EMBED_DIM,)
    np.testing.assert_array_equal(one, out[0])


# ── public API methods are sync (I4: no coroutine functions in core) ─────────
def test_memory_methods_are_sync() -> None:
    for name in ("add", "search", "get", "consolidate", "correct_memory"):
        method = getattr(Memory, name)
        assert not inspect.iscoroutinefunction(method), name
