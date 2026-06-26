# Homebrew distribution

`brew install` is the first-class macOS/Linux path for a developer CLI like Coldframe. There are two
formula styles; ship the **tap** one first, graduate to **homebrew-core** later.

## What's blocked vs ready

- **Ready now:** the formula structure, install method (isolated venv + the `[mcp]` extra so the MCP
  server works out of the box), `test do`, and caveats — all in `cold-frame.rb`.
- **Filled at release (gated on D19 — name/repo/PyPI clearance):** the `url` + `sha256` of the
  GitHub release tarball, and the `REPLACE_ME` repo/homepage. These can't be real until the name is
  final and a tag is cut.

## A. Tap release (do this first — works without PyPI)

A third-party tap may resolve dependencies from PyPI at build time, so we don't have to vendor 30
sha256-pinned `resource` blocks. `cold-frame.rb` uses this style.

1. Cut a release tag and let GitHub produce the source tarball:
   ```bash
   git tag v0.1.0 && git push origin v0.1.0
   ```
2. Fill the formula's `url`, `sha256`, and `homepage`:
   ```bash
   curl -fsSL https://github.com/<org>/cold-frame/archive/refs/tags/v0.1.0.tar.gz | shasum -a 256
   ```
3. Create the tap repo `<org>/homebrew-coldframe` and drop `cold-frame.rb` in its `Formula/`.
4. Test locally before publishing:
   ```bash
   brew install --build-from-source ./packaging/homebrew/cold-frame.rb
   brew test cold-frame
   brew audit --strict --formula ./packaging/homebrew/cold-frame.rb
   ```
5. Users then:
   ```bash
   brew install <org>/coldframe/cold-frame
   cold-frame hook install
   claude mcp add cold-frame -- cold-frame mcp
   ```

## B. homebrew-core submission (later — fully reproducible)

homebrew-core forbids network during `install`, so every dependency must be a vendored, pinned
`resource`. Generate those automatically against PyPI (do NOT hand-write them):

```bash
brew update-python-resources ./Formula/cold-frame.rb     # autofills resource blocks from PyPI
```

then replace the `def install` body with `virtualenv_install_with_resources`. The dependency set
the resource generator must reproduce (core + the `[mcp]` extra, resolved 2026-06) is pinned in
`requirements-mcp.txt` next to this file — feed it to the generator / use it as the audit target.
Native-build deps (numpy, pydantic-core, cryptography, cffi, rpds-py) build wheels under pip inside
the venv; no extra system libraries are required.

## Notes

- The formula installs the **`[mcp]` extra** so `cold-frame mcp` (the auto-capture drain + Claude
  Code memory tools) works immediately — the whole point is a one-command working setup.
- Core stays `pydantic + numpy`; everything heavier is still import-guarded, so a future
  `brew install cold-frame --without-mcp` (or a separate lean formula) is possible if wanted.
- Docker is intentionally NOT a distribution target for the local tool: the MCP server is a stdio
  subprocess Claude Code spawns, and memory is a user-owned local file (~/.cold-frame) — a container
  breaks the stdio pipe, the file ownership, and the ~/.claude hooks. Docker fits only the future
  hosted server layer (the `[server]` extra), not the local-first product.
