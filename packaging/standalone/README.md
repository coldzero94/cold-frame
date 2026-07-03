# Standalone binary

A single self-contained `cold-frame` executable that runs with **no Python installed** — the easiest
possible install for users who don't have (or don't want) a Python toolchain. Same CLI as a
from-source install, frozen with PyInstaller. This binary IS the primary distribution artifact
(ADR-D28): the Homebrew tap installs it; there is no PyPI package.

## Build

```bash
packaging/standalone/build.sh                 # → dist-bin/cold-frame  (host os/arch)
packaging/standalone/build.sh /some/out/dir   # custom output dir
```

The script builds an isolated venv with `cold-frame[mcp]` + PyInstaller, freezes one file, and
smoke-tests it offline. Verified locally on macOS arm64: ~20 MB, the CLI **and** the MCP server both
run from the single file (`cold-frame mcp` starts with the bundled mcp/anyio/starlette deps).

## What ships where

- The **binary is not committed** (platform-specific, ~20 MB). It is a CI artifact.
- PyInstaller freezes for the **host os/arch only** — run `build.sh` once on each target in CI
  (macOS arm64, Linux x86_64 — Intel Mac / macos-x86_64 was cut) and upload the two binaries to a
  GitHub Release.
- Users then `curl -fsSL .../cold-frame-macos-arm64 -o /usr/local/bin/cold-frame && chmod +x` (a
  `curl | sh` installer that picks the right asset is the natural front door).

## Known costs / gotchas

- **Size**: ~20 MB (numpy + the mcp tree). Inherent to bundling the interpreter + deps.
- **macOS Gatekeeper**: a downloaded unsigned binary is quarantined. For real distribution it must be
  **codesigned + notarized** with an Apple Developer ID (an Apple Developer account cost), else users
  hit "cannot be opened". That step is part of the release pipeline, not this script.
- **mcp.cli is excluded** on purpose: mcp's optional CLI `sys.exit(1)`s at import without `typer`,
  which breaks naive `--collect-all mcp`. We pull only the modules cold-frame imports via targeted
  `--hidden-import` + exclude the CLI. If the mcp SDK adds a new lazy import path, add it as a
  `--hidden-import` here (the binary's `cold-frame mcp` is the test).

## How this fits distribution (ADR-D28)

- This binary **is the primary artifact**: the release tag attaches it to the GitHub Release and the
  Homebrew tap (`brew install coldzero94/coldframe/cold-frame`) installs it. **There is no PyPI.**
- Install **from git source** only for the optional `[local-llm]` extra (semantic recall) that
  isn't frozen into the binary: `uv tool install "cold-frame[local-llm] @
  git+https://github.com/coldzero94/cold-frame"`.
- The raw binary (curl the Release asset) is for **reach** — non-Python users, locked-down machines,
  a zero-prerequisite demo — the same file the tap ships.
