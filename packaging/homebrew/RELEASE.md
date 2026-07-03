# Homebrew distribution

`brew install` is the first-class macOS/Linux path for a developer CLI like Coldframe. We ship a
**tap** with a **binary formula** — the release attaches a self-contained `cold-frame` binary per
platform (CLI + MCP in one file, no Python at runtime), and the formula just downloads + installs it.
NOT distributed via PyPI (ADR-D28).

> Why a binary and not an isolated-venv formula: Homebrew's build sandbox blocks network, so a
> `venv.pip_install` formula can't fetch `pydantic`/`numpy` at build time and crashes at runtime with
> `ModuleNotFoundError`. A self-contained PyInstaller binary sidesteps dependency resolution entirely.

## Cutting a release — one manual step (the tag)

```bash
git tag v0.1.1 && git push origin v0.1.1
```

That's it. The `Release` workflow (`.github/workflows/release.yml`) then, on the tag:

1. **`release`** — creates the GitHub Release (auto-generated notes).
2. **`binaries`** — builds the web UI bundle (`pnpm -C frontend build` — `_dist` is git-ignored, so
   a bare checkout has none) and then the standalone binary on each platform via
   `packaging/standalone/build.sh`, attaching `cold-frame-macos-arm64` + `cold-frame-linux-x86_64`.
   The SPA ships *inside* the binary; `build.sh` fails loudly if the bundle is missing pre-freeze and
   live-serves the frozen binary's UI post-freeze, so a release can never ship the degraded inline
   inspector by accident.
3. **`bump-tap`** — runs `packaging/homebrew/bump-tap.sh`: downloads those two assets, computes their
   `sha256`, regenerates `Formula/cold-frame.rb` in the tap repo (`coldzero94/homebrew-coldframe`)
   with the new version + urls + shas, and commits + pushes. No hand-editing of shas.

Platforms: **macOS Apple Silicon + Linux x86_64.** Intel Mac is out of scope (the free GitHub
`macos-13` Intel runners queue indefinitely, and a Rosetta cross-build hits the stock 3.9 `python3`).

### One-time setup: the tap push token

`bump-tap` pushes to a *different* repo, so the default `GITHUB_TOKEN` can't reach it. Create a
**fine-grained PAT** scoped to `coldzero94/homebrew-coldframe` with **Repository permissions →
Contents: Read and write**, then add it to the `cold-frame` repo as a secret named
**`HOMEBREW_TAP_TOKEN`**:

```bash
gh secret set HOMEBREW_TAP_TOKEN --repo coldzero94/cold-frame   # paste the PAT when prompted
```

If the secret is absent the `bump-tap` step **skips with a warning instead of failing** — the release
(binaries + GitHub Release) still succeeds; you'd just update the tap formula by hand that once.

## The in-repo formula copy

`packaging/homebrew/cold-frame.rb` is a human-readable reference/fallback. The authoritative copy is
the one `bump-tap.sh` regenerates in the tap. If you ever bump it manually, fill the sha256 with
`shasum -a 256 cold-frame-<target>`.

## Test the tap install locally

```bash
brew install coldzero94/coldframe/cold-frame
cold-frame --version
cold-frame hook install
claude mcp add cold-frame -- cold-frame mcp
```

## Notes

- The binary bundles the **`[mcp]` extra** so `cold-frame mcp` (the auto-capture drain + Claude Code
  memory tools) works immediately. It does NOT bundle the optional `[crypto]` (at-rest encryption) or
  `[local-llm]` (semantic recall) extras — those need a from-source install.
- Docker is intentionally NOT a distribution target for the local tool: the MCP server is a stdio
  subprocess Claude Code spawns, and memory is a user-owned local file (~/.cold-frame) — a container
  breaks the stdio pipe, the file ownership, and the ~/.claude hooks. Docker fits only the future
  hosted server layer (the `[server]` extra), not the local-first product.
- homebrew-core (vendored, network-free `resource` blocks) is a later option once the project is
  notable enough to be accepted; the tap is the shipping path for v1.
