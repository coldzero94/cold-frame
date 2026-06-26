---
name: remember-facts
description: Persist a durable fact the user stated — a preference, decision, convention, identity/contact detail, or a correction of something said earlier — to Coldframe memory. Use PROACTIVELY whenever the user states something worth remembering across sessions; do not wait to be asked.
---

# Remember durable facts to Coldframe

This workspace has persistent, local-first memory via Coldframe (the `add_memory` tool and automatic
recall). Recall is automatic — memories are injected at session start and per prompt, so you don't
need to search unless the user explicitly asks what you remember.

Your job is **capture**, and it costs nothing extra: doing it inside this conversation rides the
interactive session, not a separate metered budget. So:

- When the user states a **durable** fact — a preference, decision, convention, identity/contact
  detail, or a correction of an earlier fact — call `add_memory` with **one concise, self-contained
  fact** (one call per fact). Capture silently and keep going; don't announce it or ask permission.
- Do **not** capture: transient task requests, questions, chit-chat, or anything that looks like a
  secret/credential (Coldframe blocks secrets, but don't send them).
- Phrase each fact as a standalone statement (e.g. "The user deploys this repo with ship.sh", not
  "ok I'll use that"). Coldframe de-duplicates, so restating a known fact is harmless.

A keyless backstop also captures durable facts from the transcript, so anything you miss is still
caught — but your in-context judgment is higher quality, so capture what you notice.
