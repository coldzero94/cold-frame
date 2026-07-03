# Coldframe — Claude Code plugin

Wires Coldframe into Claude Code as **one install**, with no per-machine `CLAUDE.md` or
`settings.json` editing. Bundles:

- **`.mcp.json`** — registers the `cold-frame mcp` server (the `add_memory` / `search_memory` tools +
  the capture drain). `PROJECT_ROOT` is passed via `${CLAUDE_PROJECT_DIR}` so memory is scoped per
  git project.
- **`hooks/hooks.json`** — SessionStart + UserPromptSubmit recall (inject relevant memory) and a Stop
  hook that enqueues the transcript as a **keyless deterministic capture backstop**.
- **`skills/remember-facts/`** — the **agent-push** capture instruction: Claude itself extracts
  durable facts and calls `add_memory`. This runs inside your interactive session, so it uses the
  Claude you already pay for — **no API key, no separate metered cost.** (Soft — model-driven — but
  the Stop-hook backstop guarantees coverage, and dedup merges the two.)

## Prerequisite

The plugin is the integration layer; the engine is the `cold-frame` package. Install it so
`cold-frame` is on PATH (any one):

```bash
brew install coldzero94/coldframe/cold-frame
# from source (not on PyPI — ADR-D28):
#   uv tool install "cold-frame[mcp] @ git+https://github.com/coldzero94/cold-frame"
# (or a standalone binary on PATH — see packaging/standalone/)
```

## Install the plugin

```bash
claude plugin marketplace add coldzero94/cold-frame     # the marketplace hosting this plugin
claude plugin install coldframe
```

Enabling it auto-starts the MCP server + registers the hooks + loads the capture skill — nothing else
to configure. Your memory lives in `~/.cold-frame/memory.db` (one file, yours, offline).

## Cost posture

- **Recall + the Stop-hook backstop + agent-push capture** are free (recall/backstop are keyless +
  deterministic; agent-push rides your interactive session).
- For **deterministic high-quality** extraction you can opt into a model backend in the background
  worker (`cold-frame worker`): the `claude` CLI (`ClaudeCliLLM`, session auth — but metered as
  programmatic usage) or a local model (`[local-llm]`, free, heavier). These are opt-in; the
  plugin's default path costs nothing extra.

## Notes

- A plugin **cannot** ship a standing `CLAUDE.md` (a plugin-root CLAUDE.md is not loaded as context),
  so the capture instruction is a **skill** (model-invoked). `cold-frame hook install` (which wires
  the recall + capture-backstop hooks into `settings.json`, not CLAUDE.md) remains a fallback for
  users not using the plugin.
- Bundling the standalone binary in the plugin's `bin/` (so no separate `cold-frame` install) is a
  possible future enhancement; v1 references the installed `cold-frame` command.
