"""Generate a real coldframe memory-field sample: notes spread across strength bands,
each row carrying its engine-computed Strength (value/band/at_risk). Output = JSON the
p5.js hero viz consumes (data-driven, not random)."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cold_frame.api import Memory
from cold_frame.llm.base import HashEmbedder

NOW = datetime(2026, 6, 24, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


# (content, memory_type, importance, access_count, days_since_access, decay_S, pinned, confidence)
SEEDS = [
    ("I prefer dark roast coffee, no sugar", "semantic", 0.7, 18, 1, 80, True, 1.0),
    ("My partner's name is Maya", "semantic", 0.95, 30, 0, 120, True, 1.0),
    ("I lead the platform infrastructure team", "semantic", 0.85, 22, 2, 90, True, 1.0),
    ("Deploy script is now ship.sh (was deploy.sh)", "procedural", 0.8, 15, 3, 70, False, 1.0),
    ("I use vim keybindings everywhere", "semantic", 0.6, 12, 5, 60, False, 1.0),
    ("Standing desk, set to 110cm", "semantic", 0.5, 8, 7, 45, False, 1.0),
    ("Allergic to shellfish", "semantic", 0.98, 6, 14, 110, True, 1.0),
    ("Prefer async standups over live ones", "semantic", 0.55, 9, 6, 50, False, 1.0),
    ("Reading 'The Beginning of Infinity'", "episodic", 0.4, 5, 9, 35, False, 1.0),
    ("Ran 10k on Sunday, 52 min", "episodic", 0.3, 3, 12, 25, False, 1.0),
    ("Coffee chat with Jordan re: caching", "episodic", 0.35, 4, 18, 22, False, 1.0),
    ("Migrated billing to the new pipeline", "episodic", 0.6, 7, 21, 40, False, 1.0),
    ("Use ripgrep, not grep, for the monorepo", "procedural", 0.65, 14, 4, 55, False, 1.0),
    ("Postgres connection pool capped at 20", "semantic", 0.7, 10, 8, 60, False, 1.0),
    ("Sister visiting in July", "episodic", 0.5, 2, 30, 30, False, 1.0),
    ("Likes the window seat on flights", "semantic", 0.3, 4, 40, 28, False, 1.0),
    ("Old apartment was on Pine St", "episodic", 0.2, 1, 75, 18, False, 0.9),
    ("Used to work at Vessl (now Anthropic)", "episodic", 0.45, 3, 65, 30, False, 1.0),
    ("Tried oat milk latte, didn't love it", "episodic", 0.15, 1, 90, 12, False, 1.0),
    ("Considering learning Rust this quarter", "episodic", 0.4, 2, 28, 26, False, 0.5),
    ("Maybe interested in woodworking?", "episodic", 0.25, 1, 50, 15, False, 0.35),
    ("Dentist appointment was rescheduled", "episodic", 0.1, 1, 110, 8, False, 1.0),
    ("Prefers dark mode in all editors", "semantic", 0.55, 11, 10, 52, False, 1.0),
    ("Keyboard: split ergonomic, 45g switches", "semantic", 0.5, 7, 13, 44, False, 1.0),
    ("Project Coldframe ships when polished", "semantic", 0.9, 25, 1, 100, True, 1.0),
    ("Don't schedule meetings before 10am", "procedural", 0.7, 16, 3, 62, False, 1.0),
    ("Favorite tea: genmaicha", "semantic", 0.35, 5, 20, 30, False, 1.0),
    ("Birthday is March 14", "semantic", 0.85, 4, 35, 95, True, 1.0),
    ("Was a competitive swimmer in college", "episodic", 0.3, 2, 80, 22, False, 1.0),
    ("Use uv, not pip, for Python projects", "procedural", 0.75, 19, 2, 68, False, 1.0),
    ("Reviewing the Q3 roadmap this week", "episodic", 0.45, 3, 15, 32, False, 1.0),
    ("Hates being cc'd on everything", "semantic", 0.5, 6, 22, 42, False, 1.0),
    ("Lives near the river, walks at dawn", "semantic", 0.4, 5, 30, 36, False, 1.0),
    ("Cat named Pixel, very loud", "semantic", 0.6, 8, 11, 54, False, 1.0),
    ("Switched to a 4-day work week trial", "episodic", 0.55, 4, 25, 38, False, 1.0),
    ("Prefers Figma over Sketch", "semantic", 0.3, 3, 45, 26, False, 1.0),
    ("Note from a year ago about old API", "episodic", 0.15, 1, 200, 10, False, 0.8),
    ("Wants the UI to feel calm, not busy", "semantic", 0.7, 9, 6, 58, True, 1.0),
    ("Took notes app screenshot for ref", "episodic", 0.1, 1, 130, 7, False, 1.0),
    ("Owns a film camera, shoots Portra 400", "semantic", 0.35, 4, 33, 30, False, 1.0),
    ("Commit messages: imperative mood", "procedural", 0.6, 13, 5, 50, False, 1.0),
    ("Disagreed once on tabs vs spaces (spaces)", "episodic", 0.2, 2, 95, 16, False, 1.0),
    ("Saving for a trip to Kyoto", "episodic", 0.5, 3, 40, 35, False, 1.0),
    ("Drinks water from a 1L glass bottle", "semantic", 0.25, 5, 18, 24, False, 1.0),
    ("The cold-frame DB lives at ~/.cold-frame", "semantic", 0.65, 12, 4, 56, False, 1.0),
    ("Vague memory about a conference talk", "episodic", 0.12, 1, 160, 9, False, 0.3),
    ("Prefers tea in the afternoon, coffee AM", "semantic", 0.45, 7, 9, 40, False, 1.0),
    ("Pinned: emergency contact is Maya", "semantic", 0.99, 5, 50, 130, True, 1.0),
]


def main() -> None:
    d = tempfile.mkdtemp()
    m = Memory(str(Path(d) / "viz.db"), embedder=HashEmbedder(), llm=None, clock=Clock())
    rows = []
    for i, (content, mtype, imp, acc, days, decay, pinned, conf) in enumerate(SEEDS):
        res = m.create_fact(content, memory_type=mtype)
        if not res.added:
            continue
        nid = res.added[0].id
        last = NOW - timedelta(days=days)
        created = NOW - timedelta(days=days + 5)
        # paint the engine state directly so strengths span every band realistically
        m._store._conn.execute(
            "UPDATE notes SET importance=?, access_count=?, last_accessed=?, decay_S=?, "
            "pinned=?, confidence=?, created_at=? WHERE id=?",
            (imp, acc, last.isoformat(), decay, int(pinned), conf, created.isoformat(), nid),
        )
        m._store._conn.commit()
        s = m.strength(nid)
        rows.append(
            {
                "id": nid,
                "content": content,
                "type": mtype,
                "s": round(s.value, 4),
                "band": s.band,
                "atRisk": s.at_risk,
                "importance": imp,
                "access": acc,
                "pinned": bool(pinned),
                "ageDays": days,
            }
        )
    m.close()
    out = Path(__file__).parent / "memory_field.json"
    out.write_text(json.dumps(rows, indent=0))
    bands: dict[str, int] = {}
    for r in rows:
        bands[r["band"]] = bands.get(r["band"], 0) + 1
    print(f"wrote {len(rows)} notes → {out}")
    print("band spread:", bands)
    print("at_risk:", sum(1 for r in rows if r["atRisk"]), " pinned:", sum(1 for r in rows if r["pinned"]))


if __name__ == "__main__":
    main()
