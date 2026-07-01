"""Shared pydantic v2 models — the canonical wire/contract types.

Timestamps are tz-aware UTC ``datetime`` in Python; the Store serializes them to
ISO8601-UTC TEXT (SPEC §1 portability rule). G2 RATIFIED (CLAUDE.md §9):
``Status`` stays 3-value; quarantine is a flag column (``held_for_human`` /
``quarantined`` / ``triage_reason``), NOT a 4th Status value.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# The SINGLE source for note kind + lifecycle status (used everywhere — Store signatures, models,
# the UI contract enums). semantic=durable fact/preference · episodic=time-stamped event ·
# procedural=behavior directive (§7). status G2: EXACTLY 3 — active (in search) / archived
# (soft-forgotten, revivable) / deleted (purge tombstone, content scrubbed); quarantine is a flag,
# not a 4th status. Literals (not StrEnums) so hot paths + mypy stay churn-free.
MemoryTypeLiteral = Literal["semantic", "episodic", "procedural"]
StatusLiteral = Literal["active", "archived", "deleted"]
EdgeRelation = Literal["supersedes", "relates_to", "mentions", "derived_from", "caused_by"]
TriageReason = Literal[
    "true_conflict", "ambiguous_merge", "low_confidence", "pin_adjacent_archive", "consent"
]
UpdateType = Literal["extract", "dedup", "conflict", "feedback", "manual", "correct", "consolidate"]
SourceKind = Literal["message", "document", "tool", "manual"]
Band = Literal["evergreen", "budding", "fading"]
PiiCategory = Literal["email", "phone", "credit_card", "ssn"]  # the closed PII-redaction domain


class Scope(BaseModel):
    """Tenancy / isolation key. Default user is ``"default"`` (offline single-user)."""

    user_id: str = "default"
    agent_id: str | None = None
    session_id: str | None = None


class Source(BaseModel):
    """Provenance row (D-T4 invariant)."""

    kind: SourceKind
    ref: str
    role: str | None = None
    content_hash: str
    observed_at: datetime  # relative-time grounding basis


class Note(BaseModel):
    """The atomic fact — one self-contained statement (SPEC §2). ``content`` is embedded."""

    id: str
    content: str
    memory_type: MemoryTypeLiteral
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # RESERVED (dormant seam; no writer/reader in v1)
    context: str = ""
    confidence: float = 1.0  # extraction confidence (≠ importance)
    scope: Scope
    sources: list[Source] = Field(default_factory=list)
    status: StatusLiteral = "active"
    version: int = 1
    # bi-temporal (R1): created/expired = transaction axis, valid/invalid = valid axis
    created_at: datetime
    expired_at: datetime | None = None
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    # forgetting signals (R5)
    importance: float = 0.5  # long-term value
    last_accessed: datetime | None = None
    access_count: int = 0
    decay_S: float = 1.0
    # G2 quarantine flag columns (NOT a 4th Status value)
    held_for_human: bool = False  # Triage gate (provenance-less / confidence<0.4)
    quarantined: bool = False  # excluded from default search until human-promoted
    triage_reason: TriageReason | None = None
    pinned: bool = False  # ignore decay/band, top-fixed (SPEC §6)


class Edge(BaseModel):
    """Lightweight SQL edge (D7). Bi-temporal like notes."""

    src_id: str
    dst_id: str
    relation: EdgeRelation
    weight: float = 1.0
    created_at: datetime
    valid_at: datetime | None = None
    invalid_at: datetime | None = None


class Signals(BaseModel):
    """Per-hit retrieval explainability (SPEC §5)."""

    semantic: float | None = None  # cosine
    bm25: float | None = None  # normalized
    edge: float | None = None  # RESERVED: always None in v1 (the graph edge recall channel was cut)
    rrf: float  # fused rank score
    rerank: float | None = None  # LLM relevance score (set only when search(rerank=True), opt-in)


class SearchHit(BaseModel):
    """One ranked search result (a Note + its fused score + signal breakdown)."""

    note: Note
    score: float  # final fused / reranked score
    signals: Signals


class SearchResult(BaseModel):
    """Result of ``Memory.search`` (SPEC §5)."""

    hits: list[SearchHit] = Field(default_factory=list)
    used_tokens: int | None = None  # set only when token_budget given (SPEC §5 step 5)
    truncated: bool = False  # True if lower-ranked hits were dropped to fit the token budget


class Strength(BaseModel):
    """Canonical display strength S (SPEC §6 / §8.5; api-contract §4)."""

    value: float  # S ∈ [0, 1]
    band: Band
    at_risk: bool  # confidence<0.4 OR (now - last_accessed) > 60d
    imminent: bool = False  # fading sub-label: S < FADING_EMBER → archive-imminent (sub-state)


class BlockedSpan(BaseModel):
    """A span BLOCKed pre-disk (D15) — NEVER the original content. ``reason``: a deterministic
    ``secret``/``credential`` match, or ``ambiguous`` (fail-closed: the local I7 tiebreak could not
    confirm it safe — no/errored local LLM, or judged secret — NOT a confirmed detection)."""

    reason: Literal["secret", "credential", "ambiguous"]
    placeholder: str  # e.g. "[BLOCKED:aws_access_key]" — a label only; original span discarded


class RedactedSpan(BaseModel):
    """PII REDACTED inline pre-disk (opt-in, I6). Content-free: category + count, not the value."""

    category: PiiCategory
    count: int


class AddResult(BaseModel):
    """Result of ``Memory.add`` (api-contract §2.1)."""

    added: list[Note] = Field(default_factory=list)
    superseded: list[str] = Field(default_factory=list)  # ids archived by conflict
    deduped: list[str] = Field(default_factory=list)  # candidate ids merged-into-existing
    blocked: list[BlockedSpan] = Field(default_factory=list)  # secrets BLOCKed pre-disk
    redacted: list[RedactedSpan] = Field(default_factory=list)  # PII scrubbed pre-disk (opt-in)
    held: list[Note] = Field(default_factory=list)  # held_for_human (durability gate / quarantine)


class ConflictVerdict(BaseModel):
    """LLM proposal only — the engine (code) decides freshness/archive (I1).

    The LLM proposes whether two candidates are duplicates or contradictory; it NEVER
    decides supersession (that is ``valid_at`` comparison in code).
    """

    relation: Literal["duplicate", "contradiction", "unrelated"]
    confidence: float
    rationale: str = ""


class CorrectResult(BaseModel):
    """Result of ``Memory.correct_memory`` (api-contract §2.1)."""

    archived: str  # old note id (status→archived, invalid_at=now)
    new: Note  # replacement; supersedes edge old←new


class ConsolidateResult(BaseModel):
    """Result of ``Memory.consolidate`` (api-contract §2.3)."""

    reinforced: int = 0  # decay_S adjustments
    merged: list[str] = Field(default_factory=list)  # episodic clusters → semantic summary ids
    archived: list[str] = Field(default_factory=list)  # soft-archived (score<threshold or cap)
    held_for_human: list[str] = Field(default_factory=list)  # newly flagged triage items


class ImportEventsResult(BaseModel):
    """Result of ``Memory.import_events`` (I17 event-log replay)."""

    applied: int = 0  # events newly recorded (not already-stored by event_id)
    skipped: int = 0  # events skipped (already stored, non-note, or unparseable)
    materialized: int = 0  # notes upserted (highest-HLC event won LWW)


class ReembedResult(BaseModel):
    """Result of ``Memory.reembed`` — re-indexing notes under a swapped embedder (I8/I10)."""

    reembedded: int = 0  # notes whose vector was rewritten with the current embedder
    embedder_id: str = ""  # the embedder the DB now reads/writes under


class TriageItem(BaseModel):
    """One item in the human-resolution Triage queue (api-contract §2.3)."""

    note: Note
    reason: TriageReason
    candidates: list[str] = Field(default_factory=list)  # opposing / merge-candidate ids
    impact: float  # importance * recency, for ranked truncation (SPEC §6)


class ProceduralResult(BaseModel):
    """Result of ``Memory.optimize_prompt`` (api-contract §2.5)."""

    name: str
    changed: bool  # False if warrants_adjustment gate said no (drift guard)
    text: str  # current procedural content
    version: int


class ToolSpec(BaseModel):
    """A self-edit tool exposed via ``Memory.memory_tools`` (api-contract §2.4)."""

    name: Literal["create_fact", "update_fact", "supersede", "forget"]
    description: str
    input_schema: dict[str, object] = Field(default_factory=dict)
