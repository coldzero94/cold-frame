"""Embedder / LLM ABCs, the Clock seam, and the deterministic HashEmbedder.

RATIFIED (CLAUDE.md G1/G3/G6):
- **Sync** core: ``LLM.complete`` and ``Embedder.embed`` are ``def`` (the only async is mcp.py).
- ``Embedder.embed`` returns ``np.ndarray`` (shape ``(n, dim)``, float32, L2-normalized) —
  the KNN matmul path (api-contract picks np.ndarray, eval §A shape).
- Every LLM call carries a ``TaskTag`` (mock dispatch + local-only enforcement + log key).
- ``Clock`` protocol injected everywhere — engine never calls ``datetime.now()`` directly (G6).
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel

from cold_frame.constants import HASH_EMBED_DIM
from cold_frame.exceptions import PolicyError


# ── Clock seam (G6 determinism) ─────────────────────────────────────────────
@runtime_checkable
class Clock(Protocol):
    """Injected time source. Tests supply a FrozenClock; prod uses SystemClock."""

    def now(self) -> datetime: ...


class SystemClock:
    """Production clock — tz-aware UTC wall time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


# ── TaskTag (closed enum; every LLM call MUST pass one) ─────────────────────
class TaskTag(StrEnum):
    """Per-call dispatch + local-only enforcement + log key (eval §A)."""

    EXTRACT = "extract"  # write/extract.py — chat → candidate facts
    ADMISSION_TIEBREAK = "admission_tiebreak"  # ambiguous secret/PII span (MUST be local, I7)
    DEDUP_BATCH = "dedup_batch"  # write/core._dedup_judge — near-dup (judged one pair at a time)
    CONFLICT_JUDGE = "conflict_judge"  # write/core._conflict_judge — dup-vs-contradiction
    CONSOLIDATE_SUMMARY = "consolidate_summary"  # forget/consolidate.py — episodic → semantic
    RERANK_JUDGE = "rerank_judge"  # read/rerank.llm_rerank — opt-in relevance rerank (rerank=True)
    GRADIENT_DIAGNOSE = "gradient_diagnose"  # procedural/optimize.py
    GRADIENT_EDIT = "gradient_edit"  # procedural/optimize.py
    SCOPE_CLASSIFY = "scope_classify"  # api.py — global vs project tier for an auto-captured fact


# Tasks that may ONLY run on a local LLM (I7 / I-LOCAL). Fail-closed otherwise.
LOCAL_ONLY_TASKS: Final[frozenset[TaskTag]] = frozenset({TaskTag.ADMISSION_TIEBREAK})


# ── LLM result types ────────────────────────────────────────────────────────
class Usage(BaseModel):
    """Token accounting (loggable: counts only, never content)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMResult(BaseModel):
    """Result of ``LLM.complete``. ``parsed`` populated only when a schema was given."""

    text: str = ""  # raw text (schema=None path)
    parsed: BaseModel | None = None  # structured output parsed into the requested schema
    usage: Usage = Usage()
    model: str = ""


# ── LLM ABC ─────────────────────────────────────────────────────────────────
class LLM(ABC):
    """Synchronous LLM seam. Every call passes a ``TaskTag`` (eval §A, reconciled sync)."""

    name: str = ""  # "openai:gpt-4o-mini", "ollama:llama3.1", "mock"

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True for ollama/llama.cpp/mock; False for openai/anthropic."""
        ...

    @abstractmethod
    def complete(
        self,
        *,
        task: TaskTag,
        system: str,
        user: str,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResult:
        """Complete for ``task``. Returns text, or structured ``parsed`` when a schema is given."""
        ...

    def assert_local_for(self, task: TaskTag) -> None:
        """Raise ``PolicyError`` if ``task`` is local-only and this LLM is not local (I7)."""
        assert_local_for(task, self)


def assert_local_for(task: TaskTag, llm: LLM) -> None:
    """Enforce I-LOCAL in one place: a secret span NEVER reaches a remote endpoint (I7). LIVE — the
    admission tiebreak (``WriteCore._admission_block``) calls this before judging an ambiguous span,
    so an ``ADMISSION_TIEBREAK`` on a non-local LLM raises PolicyError and the span is BLOCKed
    (fail-closed) instead of sent to a remote model.
    """
    if task in LOCAL_ONLY_TASKS and not llm.is_local:
        raise PolicyError(f"task={task.value} requires a local LLM (D4/R11); got {llm.name!r}")


# ── Embedder ABC ─────────────────────────────────────────────────────────────
class EmbedderMeta(BaseModel):
    """Embedder identity stored in DB ``meta`` on first run (dim handling, I8)."""

    embedder_id: str  # "hash" | "openai:text-embedding-3-small" | "local:bge-small" ...
    dim: int


class Embedder(ABC):
    """Synchronous embedder seam. ``embed`` returns np.ndarray (KNN matmul path, G3)."""

    @property
    @abstractmethod
    def meta(self) -> EmbedderMeta:
        """id + dim; written to DB meta at migrate time (vec dim never hardcoded, I8)."""
        ...

    @property
    @abstractmethod
    def is_local(self) -> bool: ...

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """Batch embed → shape ``(len(texts), dim)``, float32, L2-normalized."""
        ...

    def embed_one(self, text: str) -> np.ndarray:
        """Single-text convenience → shape ``(dim,)``."""
        row: np.ndarray = self.embed([text])[0]
        return row


# ── HashEmbedder (default, D4 — deterministic, deps=0, no network) ──────────
class HashEmbedder(Embedder):
    """Deterministic seeded embedder: blake2b(token) → buckets, L2-normalized (I5).

    Same embedder in prod default and tests (no embedder mock needed). dim=256.
    """

    def __init__(self, dim: int = HASH_EMBED_DIM, *, name: str = "hash") -> None:
        # ``name`` overrides the embedder_id so a second deterministic embedder can stand in for
        # a different model (e.g. a local one) when exercising the re-embedding migration (I10).
        self._dim = dim
        self._meta = EmbedderMeta(embedder_id=name, dim=dim)

    @property
    def meta(self) -> EmbedderMeta:
        return self._meta

    @property
    def is_local(self) -> bool:
        return True

    def _bucket(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self._dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in text.lower().split():
                out[row, self._bucket(token)] += 1.0
            norm = math.sqrt(float(np.dot(out[row], out[row])))
            if norm > 0.0:
                out[row] /= norm
        return out
