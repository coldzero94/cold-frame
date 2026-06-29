"""Display strength S + growth band (SPEC §6 / §8.5 canonical, constants are SoT).

``S = 0.45·retrievability + 0.35·importance + 0.20·min(1, log1p(access)/log1p(20))`` with
``retrievability = e^(-Δt_last_accessed / decay_S)``. Making forgetting *visible* (the
band glyph) is the differentiator — this is the one display-strength formula (read display,
list glyph, sparkline). The ``at_risk`` overlay is band-independent.
"""

from __future__ import annotations

import math
from datetime import datetime

from cold_frame.constants import (
    ACCESS_SATURATION,
    AT_RISK_CONFIDENCE,
    AT_RISK_STALE_DAYS,
    BAND_BUDDING,
    BAND_EVERGREEN,
    FADING_EMBER,
    W_ACCESS,
    W_IMPORTANCE,
    W_RETRIEVABILITY,
)
from cold_frame.models import Band, Note, Strength


def compute_strength(note: Note, now: datetime) -> Strength:
    """Derive display strength + band + at-risk overlay for a note at ``now``."""
    ref = note.last_accessed or note.created_at
    dt_days = max(0.0, (now - ref).total_seconds() / 86400.0)
    retrievability = math.exp(-dt_days / max(note.decay_S, 1e-9))
    access_term = min(1.0, math.log1p(note.access_count) / math.log1p(ACCESS_SATURATION))
    value = (
        W_RETRIEVABILITY * retrievability + W_IMPORTANCE * note.importance + W_ACCESS * access_term
    )

    band: Band = (
        "evergreen" if value >= BAND_EVERGREEN else "budding" if value >= BAND_BUDDING else "fading"
    )
    at_risk = note.confidence < AT_RISK_CONFIDENCE or dt_days > AT_RISK_STALE_DAYS
    # FADING_EMBER sub-label: a fading note this weak is archive-imminent (a fading sub-state, NOT a
    # 4th band) — surfaced so "about to be forgotten" is visible (the decay-made-visible thesis).
    imminent = band == "fading" and value < FADING_EMBER
    return Strength(value=value, band=band, at_risk=at_risk, imminent=imminent)
