#!/usr/bin/env python3
"""Generate the README hero — the Coldframe memory field — as a self-contained SVG.

Data-driven, faithful to the live prototype (cold_frame/ui/prototype/): each ember is a real note
row; its POSITION is fixed by note id (the spatial-memory law — belief never moves it) and its HEAT
is fixed by engine-computed Strength (warm = evergreen belief, blue = fading, hex glass = pinned,
dashed frost = at-risk). Stdlib only; run `python3 assets/gen_hero.py` to regenerate the SVG.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "cold_frame" / "ui" / "prototype" / "memory_field.json"
OUT = HERE / "hero-memory-field.svg"

# ── canvas / palette (mirrors the prototype defaults) ────────────────────────────────────────────
SIZE = 1200  # the prototype's square field space
W, H = 1200, 760  # landscape hero banner
SEED = 7
YSQUASH = 0.62  # squash the square field into a landscape band
CY_OUT = 380  # vertical centre of the banner
GOLDEN = math.pi * (3 - math.sqrt(5))
BG = "#141413"
COLD = (0x6A, 0x9B, 0xCC)  # fading
EMBER = (0xD9, 0x77, 0x57)  # budding
WARM = (0xF3, 0xD9, 0xA4)  # evergreen
SLATE = (42, 54, 74)  # deep-ash floor for the coldest embers


def hash01(s: str, salt: int) -> float:
    """Deterministic 32-bit string hash → float in [0,1) — identical to the prototype's hash01."""
    h = (2166136261 ^ salt) & 0xFFFFFFFF
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0x5BD1E995) & 0xFFFFFFFF
    h ^= h >> 15
    return (h % 100000) / 100000


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def heat(s: float) -> tuple[int, int, int]:
    """Strength → colour through the three heat stops (slate → cold → ember → warm)."""
    if s < 0.5:
        return lerp(SLATE, COLD, (s / 0.5) ** 0.85)
    if s < 0.8:
        return lerp(COLD, EMBER, (s - 0.5) / 0.3)
    return lerp(EMBER, WARM, (s - 0.8) / 0.2)


def layout(mem: list[dict]) -> list[dict]:
    """Stable id-seeded phyllotaxis — position depends on note id, never on belief."""
    ranked = sorted(enumerate(mem), key=lambda p: hash01(p[1]["id"], 1))
    n = len(ranked)
    cx = cy = SIZE / 2
    max_r = SIZE * 0.43
    rot = SEED * 0.618
    out = []
    for rank, (_, d) in enumerate(ranked):
        r = math.sqrt((rank + 0.5) / n) * max_r
        a = rank * GOLDEN + rot
        jx = (hash01(d["id"], 7) - 0.5) * 26
        jy = (hash01(d["id"], 13) - 0.5) * 26
        x = cx + math.cos(a) * r + jx
        y = cy + math.sin(a) * r + jy
        out.append({"d": d, "x": x, "y": CY_OUT + (y - cy) * YSQUASH})
    return out


def hexagon(cx: float, cy: float, R: float, phase: float) -> str:
    pts = []
    for k in range(6):
        ang = k * math.pi / 3 + phase
        pts.append(f"{cx + math.cos(ang) * R:.1f},{cy + math.sin(ang) * R:.1f}")
    return " ".join(pts)


def rgb(c: tuple[int, int, int]) -> str:
    return f"rgb({c[0]},{c[1]},{c[2]})"


def build() -> str:
    mem = json.loads(DATA.read_text())
    embers = layout(mem)
    # draw dim → bright so hot embers sit on top
    embers.sort(key=lambda e: e["d"]["s"])

    defs: list[str] = []
    body: list[str] = []
    for i, e in enumerate(embers):
        d = e["d"]
        s = d["s"]
        imp = d["importance"]
        x, y = e["x"], e["y"]
        col = heat(s)
        bright = 0.28 + 0.72 * s
        base_r = 20 + s * 62 + imp * 28
        a_inner = 0.40 * bright
        core_r = 2.2 + s * 5.5 + imp * 2.5

        gid = f"b{i}"
        defs.append(
            f'<radialGradient id="{gid}" cx="50%" cy="50%" r="50%">'
            f'<stop offset="0%" stop-color="{rgb(col)}" stop-opacity="{a_inner:.3f}"/>'
            f'<stop offset="32%" stop-color="{rgb(col)}" stop-opacity="{a_inner * 0.38:.3f}"/>'
            f'<stop offset="100%" stop-color="{rgb(col)}" stop-opacity="0"/>'
            f"</radialGradient>"
        )
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{base_r:.1f}" fill="url(#{gid})"/>')

        # pinned: the cold-frame glass hex the cold can't reach
        if d["pinned"]:
            R = core_r * 3.4 + 10
            phase = hash01(d["id"], 5) * math.pi
            body.append(
                f'<polygon points="{hexagon(x, y, R, phase)}" fill="none" '
                f'stroke="rgb(230,224,214)" stroke-opacity="0.28" stroke-width="1.1"/>'
            )
        # at-risk: a faint frost ring (band-independent overlay)
        if d["atRisk"]:
            body.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{core_r * 2.2 + 6:.1f}" fill="none" '
                f'stroke="rgb(150,180,210)" stroke-opacity="0.30" stroke-width="0.9" '
                f'stroke-dasharray="1.5 3.5"/>'
            )
        # hot core: a white-gold heart tinted by heat, brighter with belief
        wc = lerp(col, (255, 248, 232), 0.55 * s)
        body.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{core_r:.1f}" '
            f'fill="{rgb(wc)}" fill-opacity="{0.92 * bright:.3f}"/>'
        )
        body.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{core_r * 0.45:.1f}" '
            f'fill="rgb(255,250,240)" fill-opacity="{0.85 * bright * s:.3f}"/>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" '
        f'aria-label="Coldframe memory field — embers whose warmth is belief strength; '
        f'blue embers are fading, hexagons shelter pinned memories">'
        f"<defs>"
        f'<radialGradient id="vignette" cx="50%" cy="44%" r="60%">'
        f'<stop offset="0%" stop-color="rgb(46,36,28)" stop-opacity="0.34"/>'
        f'<stop offset="55%" stop-color="rgb(26,23,22)" stop-opacity="0.14"/>'
        f'<stop offset="100%" stop-color="{BG}" stop-opacity="0"/>'
        f"</radialGradient>"
        + "".join(defs)
        + f"</defs>"
        f'<rect width="{W}" height="{H}" rx="16" fill="{BG}"/>'
        f'<rect width="{W}" height="{H}" rx="16" fill="url(#vignette)"/>'
        + "".join(body)
        + "</svg>"
    )


def main() -> None:
    OUT.write_text(build())
    print(f"wrote {OUT.relative_to(HERE.parent)}  ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
