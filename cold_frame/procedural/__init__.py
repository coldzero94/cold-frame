"""Procedural memory (SPEC §7, D9) — gradient prompt optimization + var-healer.

``warrants_adjustment=False`` ⇒ no edit (drift guard); a dropped f-string var ⇒
``VarHealerError`` (hard-fail).
"""

from __future__ import annotations

from cold_frame.procedural.optimize import ProceduralOptimizer

__all__ = ["ProceduralOptimizer"]
