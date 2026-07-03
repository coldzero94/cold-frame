# Contributing to coldframe

Thanks for your interest. coldframe is a local-first memory layer for AI agents — engineered by TDD
with a strict, fully-offline merge gate.

## Dev setup

```bash
git clone https://github.com/coldzero94/cold-frame && cd cold-frame
uv sync --extra dev --extra mcp        # core + dev tools + the MCP server
```

## The gate (run before every commit)

All three must be green — this is the merge bar (mirrored in CI, fully offline, no keys/network):

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy cold_frame
uv run pytest -m "not slow"             # deterministic mock-LLM + HashEmbedder tests
```

Coverage: CI enforces a floor (`--cov-fail-under=85`, core gate only — extra-gated modules like
`[crypto]`/`[local-llm]` skip there). Check it locally with
`uv run pytest -m "not slow" --cov=cold_frame --cov-report=term-missing`; if the TOTAL moves,
update the README coverage badge to match.

## How we work

- **TDD, small commits.** Write the failing test first (engine behavior → a golden case in
  `cold_frame/eval/datasets/*.yaml`; plumbing → a unit test in `tests/`), then the minimum code to
  pass, then refactor. One red→green→refactor cycle per commit, tests in the same commit.
- **Determinism is a code rule**, not just a test trick: never call `datetime.now()`/`uuid4()`
  directly — thread the injected `Clock` + id-factory (tests use `FrozenClock` + `uuid5`).
- **Style is tool-enforced** (ruff: PEP8 + isort + pyupgrade + bugbear + full type annotations;
  `mypy --strict`). Don't weaken the rules to pass — fix the code/types. `uv run pre-commit install`
  runs them automatically.
- **Invariants** (`CLAUDE.md` §3, I1–I17) are sacred — changing one needs an ADR in
  `docs/decisions.md`, not a quiet edit.
- Core depends only on `pydantic` + `numpy`; anything heavier goes behind an extra
  (`[openai]`/`[local-llm]`/`[vec]`/`[crypto]`/`[mcp]`/`[server]`), import-guarded.

## Submitting

Open an issue first for anything non-trivial. PRs should keep the gate green and include tests.
`CLAUDE.md` is the full operating manual; `docs/` holds the spec + design decisions.

## Releasing (maintainers)

Bump the version first (`pyproject.toml`, `cold_frame/__init__.py`, `tests/test_smoke.py`) and roll
the CHANGELOG. Then `git tag vX.Y.Z && git push origin vX.Y.Z` triggers
`.github/workflows/release.yml`: it creates the GitHub Release, builds a self-contained binary per
platform (macOS arm64 + Linux x86_64 — CLI, MCP server, and web UI in one file), attaches them, and
auto-bumps the Homebrew tap formula (`coldzero94/homebrew-coldframe`). Distribution is Homebrew +
direct binary download, NOT PyPI (ADR-D28). Full runbook: `packaging/homebrew/RELEASE.md`.
