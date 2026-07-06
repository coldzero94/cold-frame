"""Moat benchmark — the differentiation proof (does the engine beat a naive store?).

``recall_bench`` measures recall (table stakes — everyone does it). THIS measures the two things the
deterministic engine gives you that an append-everything + cosine-top-k store (the mem0-shaped
baseline) structurally cannot:

- **Stale-belief rate** — when a fact is corrected, does the store still surface the OLD value? The
  naive store keeps both versions active, so the superseded belief keeps showing up (and often
  ranks first — it is near-identical to the query). Coldframe archives it via deterministic
  ``valid_at`` supersession (I1/I2), so a stale belief is returned **never**.
- **Active-set size (bloat)** — the naive store grows by every write; coldframe's supersession keeps
  only the current belief active, so the set a query ranks over stays bounded (I13; consolidation +
  caps shrink it further, not measured here).

Fully deterministic + offline: the same ``HashEmbedder`` drives both systems, a fixed clock orders
the corrections, no LLM, no network, no keys. Reproducible — run
``python -m cold_frame.eval.moat_bench``. The claims are pinned in ``tests/test_moat_bench.py`` so
they cannot silently regress.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from cold_frame.api import Memory
from cold_frame.llm.base import Embedder, HashEmbedder

# A fixed-namespace, counter-based id factory: deterministic ids make search tie-breaks (and thus
# every metric) byte-reproducible across runs — the whole point of the benchmark (default uuid4
# would make current-recall wobble on ties).
_ID_NS = uuid.UUID("c01d0000-0000-4000-8000-00000000d0e5")


def _det_id_factory() -> Callable[[], str]:
    counter = 0

    def _next() -> str:
        nonlocal counter
        counter += 1
        return uuid.uuid5(_ID_NS, str(counter)).hex

    return _next


# A correction must land AFTER the fact it supersedes (I1: newer valid_at wins). The initial facts
# are back-dated to _EARLY; corrections happen at the clock's fixed _NOW (> _EARLY).
_EARLY = datetime(2026, 1, 1, tzinfo=UTC)
_NOW = datetime(2026, 6, 1, tzinfo=UTC)

# (topic, stale fact, current fact, query, stale marker, current marker). The query shares its words
# with BOTH versions (they differ only in the marker), so a similarity store cannot tell them apart
# — exactly the case deterministic supersession is for.
TOPICS: list[tuple[str, str, str, str, str, str]] = [
    ("job", "I work at Google", "I work at Anthropic", "where do I work", "Google", "Anthropic"),
    ("city", "I live in Munich", "I live in Berlin", "which city do I live in", "Munich", "Berlin"),
    ("editor", "my editor is Vim", "my editor is Emacs", "what editor do I use", "Vim", "Emacs"),
    (
        "phone",
        "my phone is an iPhone",
        "my phone is a Pixel",
        "what phone do I have",
        "iPhone",
        "Pixel",
    ),
    ("car", "I drive a Tesla", "I drive a Rivian", "what car do I drive", "Tesla", "Rivian"),
    (
        "team",
        "I am on the Growth team",
        "I am on the Platform team",
        "which team am I on",
        "Growth",
        "Platform",
    ),
    (
        "lang",
        "my main language is Go",
        "my main language is Rust",
        "what language do I use",
        "Go",
        "Rust",
    ),
    ("pet", "my pet is a cat", "my pet is a dog", "what pet do I have", "cat", "dog"),
    ("gym", "I train at Equinox", "I train at Barrys", "where do I train", "Equinox", "Barrys"),
    ("bank", "I bank with Chase", "I bank with Monzo", "who do I bank with", "Chase", "Monzo"),
]

# Unrelated facts — realistic clutter a query still has to rank over (same in both systems).
DISTRACTORS: list[str] = [
    "I prefer dark roast coffee",
    "my favorite color is teal",
    "I was born in April",
    "I speak French and Korean",
    "I enjoy hiking on weekends",
]


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b)  # HashEmbedder returns L2-normalized vectors → dot product is cosine


class NaiveStore:
    """The baseline: append every fact, retrieve by cosine top-k. No supersession, no forgetting —
    a correction just adds another row (the mem0-shaped 'store everything' strategy)."""

    def __init__(self, embedder: Embedder) -> None:
        self._e = embedder
        self._facts: list[tuple[str, np.ndarray]] = []

    def add(self, text: str) -> None:
        self._facts.append((text, self._e.embed_one(text)))

    correct = add  # a correction is just another append — the old fact stays active

    def search(self, query: str, k: int) -> list[str]:
        qv = self._e.embed_one(query)
        ranked = sorted(self._facts, key=lambda f: _cosine(qv, f[1]), reverse=True)
        return [t for t, _ in ranked[:k]]

    @property
    def active_count(self) -> int:
        return len(self._facts)


class _FixedClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


def _rate(items: list[bool]) -> float:
    return sum(items) / len(items) if items else 0.0


def evaluate(embedder: Embedder, *, k: int = 3) -> dict[str, dict[str, float]]:
    """Ingest the identical corrected-fact stream into both systems and score the moat metrics."""
    db = str(Path(tempfile.mkdtemp()) / "moat_bench.db")
    # consolidate_every high so the bloat delta is purely supersession (not auto-consolidation);
    # a deterministic id factory makes tie-breaks (and every metric) reproducible across runs.
    cf = Memory(
        db,
        embedder=embedder,
        clock=_FixedClock(_NOW),
        id_factory=_det_id_factory(),
        consolidate_every=10_000,
    )
    nv = NaiveStore(embedder)

    for _topic, stale, current, _q, _sm, _cm in TOPICS:
        fid = cf.add(stale, raw=True, observed_at=_EARLY).added[0].id
        cf.correct_memory(fid, current)  # deterministic supersession (new valid_at _NOW > _EARLY)
        nv.add(stale)
        nv.correct(current)  # the naive store keeps BOTH
    for d in DISTRACTORS:
        cf.add(d, raw=True, observed_at=_EARLY)
        nv.add(d)

    cf_stale, cf_current, nv_stale, nv_current = [], [], [], []
    for _topic, _stale, _current, q, sm, cm in TOPICS:
        cf_top = [h.note.content for h in cf.search(q, k=k).hits]
        nv_top = nv.search(q, k=k)
        cf_stale.append(any(sm in t and cm not in t for t in cf_top))
        cf_current.append(any(cm in t for t in cf_top))
        nv_stale.append(any(sm in t and cm not in t for t in nv_top))
        nv_current.append(any(cm in t for t in nv_top))

    return {
        "coldframe": {
            "stale_rate": _rate(cf_stale),  # a superseded belief surfaced in top-k
            "current_recall": _rate(cf_current),  # the corrected belief surfaced in top-k
            "active": float(len(cf.list_active(limit=1000))),
        },
        "naive": {
            "stale_rate": _rate(nv_stale),
            "current_recall": _rate(nv_current),
            "active": float(nv.active_count),
        },
        "meta": {
            "k": float(k),
            "topics": float(len(TOPICS)),
            "ingested": float(len(TOPICS) * 2 + len(DISTRACTORS)),
        },
    }


def format_report(r: dict[str, dict[str, float]]) -> str:
    cf, nv, meta = r["coldframe"], r["naive"], r["meta"]
    k, n, distractors = (
        int(meta["k"]),
        int(meta["topics"]),
        int(meta["ingested"]) - int(meta["topics"]) * 2,
    )
    return "\n".join(
        [
            f"Moat benchmark — {n} corrected facts + {distractors} distractors, recall@{k}",
            f"  {'metric':<24} {'coldframe':>9} {'naive':>9}",
            f"  {'stale-belief rate ↓':<24} {cf['stale_rate']:>9.0%} {nv['stale_rate']:>9.0%}",
            f"  {'current recall ↑':<24} {cf['current_recall']:>9.0%} {nv['current_recall']:>9.0%}",
            f"  {'active facts (bloat) ↓':<24} {int(cf['active']):>9} {int(nv['active']):>9}",
            "",
            "  → deterministic supersession (I1/I2) returns the stale belief 0% of the time and",
            "    keeps the active set bounded; the append-everything baseline surfaces every stale",
            "    belief and grows by every correction. Same embedder, same stream, no LLM.",
        ]
    )


def main() -> None:  # pragma: no cover - human-facing report
    print(format_report(evaluate(HashEmbedder())))


if __name__ == "__main__":  # pragma: no cover
    main()
