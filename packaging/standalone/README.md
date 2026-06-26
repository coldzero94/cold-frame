# Standalone binary

A single self-contained `cold-frame` executable that runs with **no Python installed** — the easiest
possible install for users who don't have (or don't want) a Python toolchain. Same CLI as
`pip install`, frozen with PyInstaller.

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
  (macOS arm64, macOS x86_64, Linux x86_64) and upload the three binaries to a GitHub Release.
- Users then `curl -fsSL .../cold-frame-macos-arm64 -o /usr/local/bin/cold-frame && chmod +x` (a
  `curl | sh` installer that picks the right asset is the natural front door).

## Known costs / gotchas

- **Size**: ~20 MB (numpy + the mcp/cryptography tree). Inherent to bundling the interpreter + deps.
- **macOS Gatekeeper**: a downloaded unsigned binary is quarantined. For real distribution it must be
  **codesigned + notarized** with an Apple Developer ID (an Apple Developer account cost), else users
  hit "cannot be opened". That step is part of the release pipeline, not this script.
- **mcp.cli is excluded** on purpose: mcp's optional CLI `sys.exit(1)`s at import without `typer`,
  which breaks naive `--collect-all mcp`. We pull only the modules cold-frame imports via targeted
  `--hidden-import` + exclude the CLI. If the mcp SDK adds a new lazy import path, add it as a
  `--hidden-import` here (the binary's `cold-frame mcp` is the test).

## When to prefer this vs pip/brew

- **pip / `uv tool install` / Homebrew** stay the primary paths for developers (smaller, updatable).
- The binary is for **reach** — non-Python users, locked-down machines, a zero-prerequisite demo.
