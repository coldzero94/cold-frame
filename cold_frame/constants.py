"""Frozen tuning constants (CLAUDE.md §3, ratified G5).

The single source of truth for every magic number in the engine. All docs reference
this module. A forgetting/dedup/conflict test cannot be written against two tables —
so there is exactly one. Every value is ``Final`` and immutable.
"""

from __future__ import annotations

from typing import Final

# ── display strength S (SPEC §6 / §8.5 canonical; api-contract §4) ──
# S = W_RETRIEVABILITY·retrievability + W_IMPORTANCE·importance + W_ACCESS·access_term
W_RETRIEVABILITY: Final[float] = 0.45
W_IMPORTANCE: Final[float] = 0.35
W_ACCESS: Final[float] = 0.20
# access_term = min(1, log1p(access_count) / log1p(ACCESS_SATURATION))
ACCESS_SATURATION: Final[float] = 20.0

# ── strength bands (3 bands; 0.10 is a fading sub-label, NOT a 4th band) ──
BAND_EVERGREEN: Final[float] = 0.66  # S >= 0.66 → evergreen 🌳
BAND_BUDDING: Final[float] = 0.33  # 0.33 <= S < 0.66 → budding 🌿; S < 0.33 → fading 🌱
FADING_EMBER: Final[float] = 0.10  # sub-label only (archive-imminent), within the fading band

# ── at-risk overlay (○, band-independent) ──
AT_RISK_CONFIDENCE: Final[float] = 0.40  # confidence < 0.40 → at_risk
AT_RISK_STALE_DAYS: Final[float] = 60.0  # (now - last_accessed) > 60d → at_risk

# ── archive-score (consolidation only, never display; api-contract §4) ──
# archive_score = ARCHIVE_W_RETRIEVABILITY·exp(-Δt/decay_S)
#               + ARCHIVE_W_IMPORTANCE·importance + ARCHIVE_W_RELEVANCE·relevance
ARCHIVE_W_RETRIEVABILITY: Final[float] = 0.50
ARCHIVE_W_IMPORTANCE: Final[float] = 0.30
ARCHIVE_W_RELEVANCE: Final[float] = 0.20
# archive fires ONLY when S < BAND_BUDDING AND archive_score < ARCHIVE_THRESHOLD, OR on cap.
ARCHIVE_THRESHOLD: Final[float] = 0.20

# ── per-scope active-note capacity caps (I13; archive lowest archive_score first) ──
CAP_SEMANTIC: Final[int] = 2000
CAP_EPISODIC: Final[int] = 500
CAP_PROCEDURAL: Final[int] = 100
# I13: pinned AND high-importance notes are NEVER archived (even over a cap).
ARCHIVE_PROTECT_IMPORTANCE: Final[float] = 0.80
# consolidation: episodic notes with pairwise cosine >= this cluster into one semantic summary.
CONSOLIDATE_CLUSTER_COSINE: Final[float] = 0.50
# cold-demote: a consolidated source's decay_S is multiplied by this (faster future forgetting).
CONSOLIDATE_DEMOTE_FACTOR: Final[float] = 0.5
# auto-maintenance: after this many new-fact writes, enqueue a (debounced) consolidate job (I13).
CONSOLIDATE_EVERY_N_WRITES: Final[int] = 20

# ── decay / reinforcement ──
REINFORCE_DECAY_INC: Final[float] = 0.5  # decay_S += 0.5 on recall (touch)
DECAY_S_CAP: Final[float] = 365.0  # decay_S clamped to [.., 365 days]

# ── retrieval fusion (read-and-budget §; SPEC §5) ──
RRF_K: Final[int] = 60  # Reciprocal Rank Fusion k_const (NO global divisor footgun)
FANOUT: Final[int] = 4  # per-signal over-fetch multiplier (k * FANOUT)
FANOUT_MIN: Final[int] = 20  # floor on per-signal candidate count
FANOUT_MAX: Final[int] = 200  # ceiling on per-signal candidate count
# edge promiscuity down-weight: 1 / (1 + EDGE_PROMISCUITY_PENALTY·(n-1)^2)
# RESERVED — for the dormant RRF edge channel (not wired in v1; see read/fuse.py).
EDGE_PROMISCUITY_PENALTY: Final[float] = 0.001
# (meta boost lives in read/rerank.py with its own +15% factor clamp — no constant needed here)

# ── dedup cosine bands (SPEC §4 / §6 Triage; api-contract) ──
DEDUP_NEAR_DUP: Final[float] = 0.82  # cosine >= 0.82 → near-dup candidate (LLM band floor)
DEDUP_AUTO_MERGE: Final[float] = 0.93  # cosine >= 0.93 → auto-merge (no LLM)
# the [0.82, 0.93) band is the ONLY band sent to the conflict/dedup LLM.
MINHASH_THRESHOLD: Final[float] = 0.90  # MinHash Jaccard exact-ish dedup gate
# conflict-candidate retrieval floor: same-subject contradictions ("works at X" vs
# "works at Y") sit BELOW the dedup band (~0.75), so the CONFLICT judge casts a wider net.
CONFLICT_CANDIDATE_FLOOR: Final[float] = 0.50

# ── importance feedback EMA ──
IMPORTANCE_EMA_ALPHA: Final[float] = 0.10

# ── confidence / provenance gates (I14) ──
CONFIDENCE_FLOOR: Final[float] = 0.40  # < 0.40 → quarantine (held_for_human), no provenance req

# ── embedding ──
HASH_EMBED_DIM: Final[int] = 256  # HashEmbedder dimension (deterministic, deps=0)
EMBED_METRIC: Final[str] = "cosine"

# ── note granularity (SPEC §2) ──
NOTE_MIN_CHARS: Final[int] = 15
NOTE_MAX_CHARS: Final[int] = 80

# ── durable jobs queue (I12; data-layer §3.3) ──
LEASE_TTL: Final[float] = 300.0  # seconds; stale running-job reclaim threshold
MAX_ATTEMPTS: Final[int] = 5  # attempts cap → dead-letter
RETRY_BACKOFF_BASE: Final[float] = 0.05  # seconds; exponential backoff base (0.05·2^attempt)
# NOTE: no app-level SQLITE_BUSY retry constant. Concurrency = WAL + busy_timeout=5s (interactive
# writes wait for the lock at BEGIN IMMEDIATE) + the durable jobs queue (retries background work). A
# retry wrapper only helps beyond a 5s-held lock — unrealistic for a local single-user tool; add one
# if contention ever shows up.

# ── access_log retention (R5; data-layer §1.2) ──
ACCESS_LOG_CAP_PER_NOTE: Final[int] = 50  # keep at most 50 most-recent rows per note
ACCESS_LOG_DOWNSAMPLE_DAYS: Final[float] = 90.0  # rows older than 90d → 1/day collapse

# ── concurrency PRAGMAs (data-layer §3.1) ──
BUSY_TIMEOUT_MS: Final[int] = 5000
WAL_AUTOCHECKPOINT: Final[int] = 1000

# ── schema ──
SCHEMA_VERSION: Final[int] = 1
