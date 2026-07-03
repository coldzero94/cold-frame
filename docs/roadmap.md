# Coldframe roadmap — post v0.1.1

> Forward-looking; grounded in the v0.1.1 codebase + a multi-axis audit (moat, deferred features,
> adoption, engineering health). Current shipped state lives in the root `CLAUDE.md` Status section;
> decisions are ADRs in [`decisions.md`](decisions.md). This is a direction doc, not a contract.

## The one bet that defines the product

**The headline feature does not run in the default configuration.** Contradiction *detection* is
LLM-gated, and the only LLM that ships is remote (`ClaudeCliLLM`), so in the zero-key offline
default the engine only merges exact/near-duplicates — it never resolves a real contradiction
("works at X" vs "works at Y"). And the differentiator ("we forget / consolidate / resolve
conflicts better — by hand, proven by tests, the discipline A-MEM/langmem skipped") has **never
been measured against a competitor**: `eval/recall_bench.py` only compares hash-vs-local recall.

Making the moat **(a) run in the default and (b) provable as a number** matters more than anything
else below. Everything else is hygiene and reach.

## Now — next 1–2 weeks (debt already biting)

1. **⭐ Single-source the package version.** `0.1.1` is hardcoded in three places
   (`pyproject.toml`, `cold_frame/__init__.py`, `tests/test_smoke.py`), and the only guard
   (`release.yml`) checks tag-vs-pyproject only — this literally reddened `main` on the v0.1.1 cut.
   Make `__init__.__version__` the sole source (pyproject reads it via hatch dynamic version; the
   smoke test asserts equality with the installed dist metadata; the release guard reads `__init__`).
   **Effort: S.**
2. **Resolve the dormant `[vec]` / `[server]` extras.** Both install real deps that do nothing
   (`[vec]` has zero code wiring; `[server]` pulls fastapi/psycopg for a PostgresStore that is a
   docstring). `pip install 'cold-frame[server]'` advertises a capability that does not exist —
   mark them RESERVED/deferred in `pyproject`. **Effort: S.**
3. **Extend the CI matrix.** Ubuntu-only 3.12/3.13 today, but the classifiers claim 3.14, local dev
   is 3.14, and the release ships a `macos-arm64` binary with **zero macOS CI**. Add a `macos-14`
   cell and a 3.14 cell; add a version-consistency check to `ci.yml` (not just `release.yml`).
   **Effort: S.**
4. **Raise coverage on `mcp.py` (63%).** It is the agent-facing security boundary (threat model,
   security-spec §4) and the lowest-covered module: the error→code map (`mcp_code_for`), the
   capture-drain failure path, the `build_server` import-guard degrade, and `main()` are untested.
   **Effort: M.**

## Next — this quarter (moat + adoption bets)

1. **⭐⭐ Close the inert-moat gap and prove it. [THE BET]** (a) Make contradiction detection reachable
   without a remote LLM — a deterministic same-subject contradiction heuristic, or ship the
   recommended `bge-small` embedder as the default so a semantic conflict is even a candidate.
   (b) Build a **reproducible comparative benchmark** (coldframe vs mem0 / Letta on a shared
   recall + contradiction task) that turns "better memory" into a number. That benchmark is also
   the single highest-leverage adoption asset the project does not yet have. **Effort: L.**
2. **Dogfood + measure the auto-memory loop.** The product bet (D26, automatic recall + capture in
   Claude Code) has unproven real-world quality: MCP sampling is unsupported, so capture is
   agent-push via a plugin skill + a naive backstop, and the plugin install path was noted as
   live-unverified. Verify the one-command onboarding end-to-end and instrument capture
   precision/recall on real sessions. **Effort: M.** *(This is effectively a precondition for the
   bet above — see risk.)*
3. **Perf-smoke + a nightly scheduled runner.** The D.1 latency budgets are documented but "NOT
   gated," `tests/perf/` does not exist, and no `schedule:` trigger runs `-m slow` anywhere — the
   marker is dead. Add a loose p95 perf-smoke and the repo's first scheduled job. This latency
   ceiling is what would later justify the `[vec]` ANN flip. **Effort: M.**
4. **Test-enforce the frozen constants against the docs.** The UI wire contract cannot drift
   (codegen-drift CI), but the moat's numeric core (strength weights, bands, caps, RRF k) can drift
   silently between `constants.py` and the docs that quote it. Extend the same proof-chain to the
   constants so a formula divergence fails CI — the moat as a machine-checked invariant, not prose.
   **Effort: M.**
5. **Decide the distribution reach.** Brew-binary + git-source only (ADR-D28 dropped PyPI) excludes
   Windows, `pip`/`uv`, and Docker/CI installs. Reconsider a PyPI wheel (the 2FA blocker) and/or a
   Windows binary — a product call, not just code. **Effort: M.**

## Later — bigger swings

- **Hosted / team memory layer** (`[server]` + `PostgresStore`) — the natural product expansion
  (shared/team memory). Blocked on resolving the `agent_id`-as-tier overload first (decisions.md
  records the exit condition: add a real `Scope.project`/`tier` field + migration). **Effort: XL.**
- **Cross-device sync beyond last-writer-wins** (CRDT-ish) — the one genuinely-deferred feature left
  after the D29 crypto removal. **Effort: L.**
- **Semantic recall as the default** (currently opt-in `[local-llm]`) — gated on shipping a small
  model without bloating the binary. **Effort: M/L.**
- **Supply-chain hygiene** — SBOM + build attestation on release (security-spec §6 asks for it;
  `release.yml` ships binaries with no provenance). Matters for `brew`/`curl`-installed binaries.
  **Effort: M.**

## If you only do three things

1. **Single-source the version** — end the recurring red-main footgun.
2. **Close the inert-moat** — make deterministic conflict resolution actually run in the zero-key
   default; today the headline feature is dark.
3. **Build the comparative benchmark** — turn "better memory" from a claim into a number.

## Biggest risk to the whole plan

The entire positioning rests on "automatic memory in Claude Code is good," and that quality is
unproven. If the auto-loop does not capture the right facts and recall them usefully in real use,
the engineering underneath does not matter. That is why *Next #2 (dogfood + measure the loop)* is a
precondition for the moat bet, not a parallel nice-to-have.
