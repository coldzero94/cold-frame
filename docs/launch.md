# Launch playbook (DRAFT)

> **Do not post any of this until the product is launch-ready:** (1) a one-time LIVE verification in
> a real Claude Code session (recall + capture actually work), and (2) a tagged release on PyPI so
> the install commands resolve. Marketing an unverified product in front of a dominant incumbent is
> the fastest way to lose credibility. The drafts below are starting points — edit to your voice.

## The competitive reality (verified)

You are **not** entering an empty category. **claude-mem** (thedotmack) is a near-identical Claude
Code memory tool, **Apache-2.0** (NOT AGPL — verified via the GitHub API), ~85K stars. So:

- "First/only local memory for Claude Code" is **not** a true hook, and there is **no license wedge**.
- The honest, verifiable differentiators are narrower but real: **rewindable bi-temporal belief**
  (correct a fact, query "what did I believe last month"), a **bounded/deterministic** store
  (dedup + decay + caps — it doesn't hoard everything), **offline + no key**, and a broader surface
  (**MCP server + Python lib + CLI**, not only a plugin).
- Lead with the **rewind "money shot"** ([`demo.md`](demo.md), §B) — it's the unique, undeniable thing.

## Positioning

One-liner:
> **Local-first memory for your AI agent — and one you can rewind.** It remembers you across
> sessions, and when a fact changes you can ask what you believed *before*. One SQLite file you own,
> offline, no API key.

Don't: claim "first", use superlatives, dodge "how is this different from claude-mem?" (candor wins),
or over-index on "no key / rides your Claude" vs claude-mem (it's also local — true vs cloud mem0).

## Channels (ranked, solo dev, ~zero budget)

1. **Claude Code plugin marketplace** — your own repo (done) + submit to the Anthropic community
   directory (the "verified" badge helps amid the unverified-plugin scare).
2. **MCP registries** — mcp.so / smithery / glama + a PR to punkpeye/awesome-mcp-servers.
3. **Show HN** — highest single-shot reach for OSS dev tools; brutal, so nail the first comment.
4. **Reddit** r/ClaudeAI · r/mcp · r/LocalLLaMA (+ r/selfhosted) — 90/10 rule, value posts not pitches.
5. **X build-in-public** — best *sustained* channel; threads; expect slow compounding from zero.
   Product Hunt later (after you have an audience).

## Drafts

### Show HN
**Title:** `Show HN: Coldframe – local, rewindable memory for AI coding agents`

**First comment:** I built Coldframe: a local-first memory layer for AI coding agents. Your agent
forgets you between sessions; this remembers — one SQLite file you own (`~/.cold-frame/memory.db`),
offline, no API key. What's different: most memory tools live in the cloud or capture *everything* an
agent did. Coldframe keeps a **deterministic, bounded belief store** (dedups, decays, caps — doesn't
hoard) and is **bi-temporal, so you can rewind**: correct a fact and ask "what did I believe last
month?" Capture rides the Claude you already pay for (the agent calls a tool; keyless deterministic
backstop), and it's also a Python lib + CLI + MCP server. Honest: **claude-mem** is excellent and far
bigger (also Apache + local) — Coldframe's bet is the opposite: *selective, owned, rewindable*
belief. Early (pre-1.0, just public). Feedback very welcome. Repo: github.com/coldzero94/cold-frame
*(Be at the keyboard the first 60–90 min; reply graciously; the "vs claude-mem" answer is pre-loaded above; never orchestrate upvotes.)*

### X launch thread
1. Your AI coding agent forgets everything between sessions. I built **Coldframe** — local-first
   memory it can recall *and rewind*. One SQLite file, offline, no API key. 🧵 [rewind demo GIF]
2. Most memory tools are cloud, or log everything. Coldframe keeps a **bounded, deterministic
   belief** — dedups, decays, caps. It doesn't hoard.
3. The part nothing else does: **rewind**. Correct a fact → ask "what did I believe in March?" →
   `cold-frame search "deploy" --as-of 2026-03-01`.
4. It rides the Claude you already pay for — the agent captures facts itself, no extra key/cost. A
   keyless deterministic backstop means nothing's missed.
5. Also an MCP server + Python lib + CLI. Apache-2.0, early + open.
   `claude plugin install coldframe@coldframe` → github.com/coldzero94/cold-frame. Feedback 🙏

### Reddit (r/ClaudeAI / r/LocalLLaMA) — a *value* post, not a pitch
**Title:** I built a local, *rewindable* memory for Claude Code — and what I learned about bounding it.
**Body:** Open by crediting claude-mem; explain the axis you wanted to solve differently (rewindable
belief + not hoarding everything); share one design insight (why a bi-temporal belief store, not an
observation log); link last. 90/10: insight first, promo last.

## Sequenced checklist

- [ ] **Gate 0** — LIVE verify in real Claude Code (recall + capture work); `claude plugin validate`
      clean. *(Blocks everything below.)*
- [ ] **Release** — register the PyPI trusted publisher, `git tag v0.1.0` (the Release workflow does
      PyPI + binaries). Confirm `uv tool install` / `claude plugin install` work on a clean machine.
- [ ] **Pre-launch** — README rewind GIF (record [`demo.md`](demo.md) §B); MCP registries; submit to
      the Anthropic community directory; seed the X account with 3–5 build-in-public posts.
- [ ] **Soft launch** — r/ClaudeAI + r/mcp value posts; fix the top-3 friction points.
- [ ] **Main launch** (Tue–Thu AM ET) — Show HN + the X thread; pre-written "vs claude-mem" reply.
- [ ] **Sustain** — 3–5 substantive X posts/week; be the helpful voice in the subreddits; chase
      integrations (the mem0 lesson).
