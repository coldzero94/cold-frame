# Publishing the Coldframe plugin to a marketplace

How to make `claude plugin install coldframe` work for users. Verified against `claude plugin
validate` (passes). Authoritative as of 2026-06-27; re-check the docs at release.

## Layout (this repo IS the marketplace)

A single git repo can be both the marketplace and host the plugin. Coldframe uses:

```
<repo root>/
├── .claude-plugin/
│   └── marketplace.json          # the marketplace catalog (lists the plugin below)
└── packaging/plugin/             # the plugin itself
    ├── .claude-plugin/plugin.json
    ├── .mcp.json
    ├── hooks/hooks.json
    └── skills/remember-facts/SKILL.md
```

- `.claude-plugin/marketplace.json` (repo root) lists the plugin with `source: "./packaging/plugin"`.
- `packaging/plugin/.claude-plugin/plugin.json` is the plugin manifest.

## Release steps

1. **Validate locally** (catches manifest/schema errors before anyone installs):
   ```bash
   claude plugin validate .
   ```
2. **Bump the version** in `packaging/plugin/.claude-plugin/plugin.json` for a release (or omit
   `version` so every commit SHA is its own version during active development).
3. **Push to the public repo** (the name/repo is gated on D19 — name/trademark clearance):
   ```bash
   git push    # to github.com/coldzero94/cold-frame
   ```
4. **Users install** (one-time marketplace add, then install):
   ```bash
   claude plugin marketplace add coldzero94/cold-frame      # GitHub shorthand; or a full git URL
   claude plugin install coldframe@coldframe        # plugin@marketplace
   ```
   Prerequisite: the `cold-frame` CLI must be on PATH (`uv tool install "cold-frame[mcp]"` / brew /
   the standalone binary) — the plugin is the integration layer, the package is the engine.
5. **Manage / update**:
   ```bash
   claude plugin list
   claude plugin marketplace update coldframe       # pull the latest
   claude plugin disable coldframe@coldframe
   ```
   (Claude Code also checks for plugin updates in the background at session start.)

## Submitting to Anthropic's community marketplace (optional, wider reach)

Once the name clears, Coldframe can be listed in `anthropics/claude-plugins-community`:

1. `claude plugin validate .` clean.
2. Submit via the Console form: <https://platform.claude.com/plugins/submit> (individual) or the
   claude.ai admin directory (Team/Enterprise).
3. Automated safety screening + review; on approval the plugin is pinned to a commit SHA in the
   community repo, CI bumps the pin on new commits, the public catalog syncs nightly.
4. Users then: `claude plugin marketplace add anthropics/claude-plugins-community` →
   `claude plugin install coldframe@claude-community`.

## Blocked on

The marketplace `add` URL needs the final repo/org, which depends on **D19** (name/PyPI/trademark
clearance). The manifests + structure are ready and validate now; only the public repo URL is
pending.
