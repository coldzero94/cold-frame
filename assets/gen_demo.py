#!/usr/bin/env python3
"""Generate assets/demo.svg — a static terminal card of a real, offline Coldframe session.

Faithful to actual CLI output (captured against a throwaway DB). It's the immediate README demo;
`packaging/demo/demo.tape` produces an animated GIF of the same session via charmbracelet/vhs.
Stdlib only; run `python3 assets/gen_demo.py` to regenerate.
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent / "demo.svg"

# palette (a calm dark terminal)
BG = "#16161a"
CHROME = "#1f1f26"
PROMPT = "#7fb069"  # green $
CMD = "#e8e6e3"  # command text
OUT_C = "#9a97a0"  # plain output
CMT = "#6a6772"  # muted comment
EVER = "#e0a86b"  # evergreen band (warm)
BUD = "#7aa7d6"  # budding band (cool)
OKC = "#7fb069"

FONT = "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace"
FS = 17
LH = 29
BODY_W = 1160
PAD_X = 30
PAD_TOP = 66  # room for the title bar
PAD_BOT = 26

# Each line is a list of (text, color) segments. A leading "$ " is the prompt.
Seg = list[tuple[str, str]]


def cmd(text: str) -> Seg:
    return [("$ ", PROMPT), (text, CMD)]


LINES: list[Seg] = [
    cmd('cold-frame add "I prefer dark roast coffee, no sugar"'),
    [("+ 0c05f1fe  I prefer dark roast coffee, no sugar", OUT_C)],
    cmd('cold-frame add "Deploy script is ship.sh now (was deploy.sh)"'),
    [("+ da91ad6d  Deploy script is ship.sh now (was deploy.sh)", OUT_C)],
    [],
    [("# a new session, days later — your agent still knows:", CMT)],
    cmd('cold-frame search "dark roast coffee"'),
    [("0.037  0c05f1fe  I prefer dark roast coffee, no sugar", OUT_C)],
    [],
    [("# it tracks belief strength and forgets the weak — see the bands:", CMT)],
    cmd("cold-frame list"),
    [("0c05f1fe  ", OUT_C), ("[evergreen] ", EVER), (" I prefer dark roast coffee, no sugar", OUT_C)],
    [("da91ad6d  ", OUT_C), ("[budding]   ", BUD), (" Deploy script is ship.sh now (was deploy.sh)", OUT_C)],
    [],
    [("# one file you own — offline, no key, integrity-checked:", CMT)],
    cmd("cold-frame doctor"),
    [("notes=2 fts=2 vec=2  integrity=ok  encryption=off  ", OUT_C), ("ok", OKC)],
]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build() -> str:
    width = BODY_W
    height = PAD_TOP + len(LINES) * LH + PAD_BOT

    rows: list[str] = []
    for i, segs in enumerate(LINES):
        y = PAD_TOP + i * LH
        if not segs:
            continue
        tspans = "".join(
            f'<tspan xml:space="preserve" fill="{c}">{esc(t)}</tspan>' for t, c in segs
        )
        rows.append(f'<text x="{PAD_X}" y="{y}" font-family="{FONT}" font-size="{FS}">{tspans}</text>')

    dots = "".join(
        f'<circle cx="{28 + k * 22}" cy="26" r="6.5" fill="{c}"/>'
        for k, c in enumerate(["#ff5f57", "#febc2e", "#28c840"])
    )
    title = (
        f'<text x="{width / 2}" y="31" text-anchor="middle" font-family="{FONT}" '
        f'font-size="14" fill="#6a6772">coldframe · one file, offline, no key</text>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="Terminal demo: cold-frame add, search, list, doctor — offline, one SQLite file">'
        f'<rect width="{width}" height="{height}" rx="12" fill="{BG}"/>'
        f'<rect width="{width}" height="46" rx="12" fill="{CHROME}"/>'
        f'<rect y="34" width="{width}" height="12" fill="{CHROME}"/>'
        f"{dots}{title}"
        + "".join(rows)
        + "</svg>"
    )


def main() -> None:
    OUT.write_text(build())
    print(f"wrote {OUT.name}  ({OUT.stat().st_size} bytes, {len(LINES)} lines)")


if __name__ == "__main__":
    main()
