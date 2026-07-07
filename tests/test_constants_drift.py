"""Frozen-constants drift guard — the moat's numbers, pinned to the docs (like the UI contract).

CLAUDE.md's "Frozen constants" block is declared the single-source mirror of constants.py, but it is
hand-copied prose — a strength weight or a cap could drift silently between code and doc.
The UI wire contract can't drift (codegen-drift CI); this extends the same discipline to the moat's
numbers, so a divergence fails CI instead of the docs quietly lying. Comparison is numeric
(format-agnostic: 0.20 == 0.2), and every pattern MUST match — a reformat of the block that breaks a
pattern is a deliberate change that must update this list too.
"""

from __future__ import annotations

import re
from pathlib import Path

from cold_frame.constants import (
    ARCHIVE_THRESHOLD,
    ARCHIVE_W_IMPORTANCE,
    ARCHIVE_W_RELEVANCE,
    ARCHIVE_W_RETRIEVABILITY,
    AT_RISK_CONFIDENCE,
    AT_RISK_STALE_DAYS,
    BAND_BUDDING,
    BAND_EVERGREEN,
    CAP_EPISODIC,
    CAP_PROCEDURAL,
    CAP_SEMANTIC,
    DECAY_S_CAP,
    DEDUP_AUTO_MERGE,
    DEDUP_NEAR_DUP,
    EDGE_PROMISCUITY_PENALTY,
    EDGE_SEED_K,
    FADING_EMBER,
    FANOUT,
    FANOUT_MAX,
    FANOUT_MIN,
    HASH_EMBED_DIM,
    REINFORCE_DECAY_INC,
    RRF_K,
)

_CLAUDE_MD = Path(__file__).resolve().parent.parent / "CLAUDE.md"


def _frozen_block() -> str:
    text = _CLAUDE_MD.read_text(encoding="utf-8")
    start = text.index("**Frozen constants**")
    end = text.index("\n---", start)
    return text[start:end]


def test_frozen_constants_block_matches_constants_py() -> None:
    block = _frozen_block()
    checks: list[tuple[str, float]] = [
        # strength weights (the moat formula) — S = 0.45·retr + 0.35·imp + 0.20·min(...)
        (r"S = ([\d.]+)·retrievability", 0.45),
        (r"\+ ([\d.]+)·importance", 0.35),
        (r"importance \+ ([\d.]+)·min", 0.20),
        # bands
        (r"S≥([\d.]+)", BAND_EVERGREEN),
        (r"([\d.]+)≤S<", BAND_BUDDING),
        (r"FADING_EMBER=([\d.]+)", FADING_EMBER),
        # at-risk overlay
        (r"confidence<([\d.]+)", AT_RISK_CONFIDENCE),
        (r">([\d.]+)d", AT_RISK_STALE_DAYS),
        # archive
        (r"ARCHIVE_THRESHOLD=([\d.]+)", ARCHIVE_THRESHOLD),
        (r"archive_score weights `([\d.]+)/", ARCHIVE_W_RETRIEVABILITY),
        (r"archive_score weights `[\d.]+/([\d.]+)/", ARCHIVE_W_IMPORTANCE),
        (r"archive_score weights `[\d.]+/[\d.]+/([\d.]+)`", ARCHIVE_W_RELEVANCE),
        # per-scope caps
        (r"semantic=([\d.]+)", CAP_SEMANTIC),
        (r"episodic=([\d.]+)", CAP_EPISODIC),
        (r"procedural=([\d.]+)", CAP_PROCEDURAL),
        # decay / reinforce
        (r"REINFORCE_DECAY_INC=([\d.]+)", REINFORCE_DECAY_INC),
        (r"DECAY_S_CAP=([\d.]+)", DECAY_S_CAP),
        # retrieval fusion
        (r"k_const=([\d.]+)", RRF_K),
        (r"FANOUT=([\d.]+)", FANOUT),
        (r"min ([\d.]+), max", FANOUT_MIN),
        (r"max ([\d.]+)\)", FANOUT_MAX),
        # dedup cosine bands `0.82`/`0.93`
        (r"dedup bands `([\d.]+)`", DEDUP_NEAR_DUP),
        (r"dedup bands `[\d.]+`/`([\d.]+)`", DEDUP_AUTO_MERGE),
        # embedder + reserved edge constants
        (r"dim=([\d.]+)", HASH_EMBED_DIM),
        (r"EDGE_SEED_K=([\d.]+)", EDGE_SEED_K),
        (r"EDGE_PROMISCUITY_PENALTY=([\d.]+)", EDGE_PROMISCUITY_PENALTY),
    ]
    for pattern, expected in checks:
        m = re.search(pattern, block)
        assert m is not None, (
            f"pattern not found in the frozen-constants block (reformatted?): {pattern}"
        )
        assert float(m.group(1)) == float(expected), (
            f"drift: doc {m.group(1)!r} != code {expected} for {pattern!r}"
        )
