# Demo / recording script

Two demos. The first shows the everyday product; the second shows the **differentiator** — a memory
you can *rewind* — which capture-everything tools can't. All commands below are real + verified.

## A. The product (Claude Code) — the 20-second "it remembered" clip

The story: state something once, get it back in a fresh session without re-explaining.

1. **Setup** (once):
   ```bash
   uv tool install "cold-frame[mcp] @ git+https://github.com/coldzero94/cold-frame"
   claude plugin marketplace add coldzero94/cold-frame && claude plugin install coldframe@coldframe
   ```
2. **Session 1** — in Claude Code, say something durable: *"I always deploy this repo with ship.sh,
   never deploy.sh."* End the session.
3. **Session 2** (new window) — ask: *"how do I deploy here?"* → Claude answers **ship.sh** with no
   re-explaining (recall was injected at session start). That's the clip.

## B. The differentiator — rewindable belief (the "money shot")

Capture-everything memory tools log what happened; coldframe keeps a *belief you can correct and
rewind*. Recorded reliably from the terminal + a short Python snippet:

```bash
export COLD_FRAME_DB=/tmp/demo.db
```
State a belief, then change your mind (in a real session the agent does this itself; here, the
library, deterministically). The original is back-dated to March so the rewind has a window to land
in — it stands for a fact that's been true since then:
```python
from datetime import UTC, datetime
from cold_frame import Memory, Scope
m = Memory("/tmp/demo.db"); s = Scope(user_id="default")
m.add("I deploy with deploy.sh", scope=s, observed_at=datetime(2026, 3, 1, tzinfo=UTC))  # true since March
old = m.list_active(scope=s)[0].id
new = m.correct_memory(old, "I deploy with ship.sh now", scope=s).new.id  # old archived; new valid from now
print("old:", old); print("new:", new)   # copy these two ids for the `path` line below
```
Now the rewind — the line no observation-log can show:
```bash
cold-frame search "deploy"                       # → I deploy with ship.sh now   (current belief)
cold-frame search "deploy" --as-of 2026-05-01    # a date before the change      → I deploy with deploy.sh (the OLD belief)
cold-frame path <new> <old>                      # <new> ->[supersedes] <old>    (the belief lineage)
```

> The point in one line: **"What did I believe back in March?"** — coldframe answers it; a
> capture-everything log doesn't.

## Recording tips (for the GIF)

- Keep it **10–15s** (README GIFs / X clips lose people after that). One workflow, start to finish —
  don't tour features.
- A free, OSS recorder like **OpenScreen** works well; auto-zoom the active area, show keystrokes,
  clean terminal, no dead air.
- Lead every post/README with demo **B**'s rewind frame — it's the unique, undeniable thing.
