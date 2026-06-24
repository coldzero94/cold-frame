# UI prototype — the Memory Field hero visualization

The **hero** of the Coldframe UI is *state*, not topology (CLAUDE.md anti-patterns): forgetting
made visible. This folder is the design prototype for that hero, built with the **algorithmic-art**
skill (`.claude/skills/algorithmic-art`).

## What's here

| file | what it is |
|---|---|
| `thermal-persistence.md` | the **algorithmic philosophy** — the "Thermal Persistence" movement. Memories as embers; warmth = belief; the cold = forgetting. The hidden conceptual seed is the gardener's *cold frame*: a glass-topped box that traps the day's heat so seedlings survive the cold night — exactly what a *pinned* memory is. |
| `memory-field.html` | the self-contained interactive artifact (p5.js from CDN). Open it in any browser. |
| `memory-field.png` | a rendered reference frame. |
| `memory_field.json` | the **real** engine snapshot it's driven by (48 notes). |
| `gen_sample.py` | regenerates that snapshot from the live engine. |

## Why it fits Coldframe (not generic generative art)

It is **data-driven**, not random — every visual quantity maps to a real engine value:

- **position** — fixed forever by a hash of the note `id` (id-seeded phyllotaxis). This honors the
  product's design law: *opacity / size / heat only, never reposition* (spatial memory). The `seed`
  control only rotates/relaxes the whole field — "the same mind from a different angle."
- **heat (color + glow + core)** — the note's display **strength `S`** and **band**
  (evergreen → warm gold, budding → ember orange, fading → cold slate-blue).
- **shimmer amplitude** — couples to belief: evergreen embers hold steady; **at-risk** ones gutter
  and grow a trembling **frost** ring (band-independent overlay).
- **cold-frame hexagon** — drawn only around **pinned** notes: the glass that shields them from decay.

Hover any ember to read the actual note (content / band / S / type / access).

## Regenerate the data

```bash
uv run python cold_frame/ui/prototype/gen_sample.py   # writes memory_field.json
# then re-inline it into memory-field.html's /*__MEMORY__*/ … /*__END__*/ slot
```

## Path to the full Vue SPA (`[ui]` extra)

This prototype is the reference for one Vue component (`<MemoryField>`): the same p5.js sketch,
fed live by the local UI server's JSON API (`list_active` + `compute_strength`) instead of a baked
snapshot. The conventional app shell around it (routing, the triage queue, fact inspector, search)
is standard Vue and does not come from this skill — the skill owns the **hero canvas** only.
