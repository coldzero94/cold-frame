"""``cold-frame`` CLI (SPEC §9). Entry point: ``cold-frame = "cold_frame.cli:main"``.

Offline-first (I5): ``add``/``search`` work with zero keys/network. Every subcommand is wired —
``add search list show stats timeline path doctor consolidate worker jobs export import ui mcp
setup purge reembed hook``. The DB location resolves ``--db`` → ``$COLD_FRAME_DB`` →
``branding.DB_PATH`` (no literal path strings, branding rule).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from cold_frame import __version__, branding
from cold_frame.api import Memory
from cold_frame.branding import PKG
from cold_frame.constants import NOTE_MAX_CHARS
from cold_frame.exceptions import ColdFrameError, NoteNotFound, StoreError
from cold_frame.integrations.claude_code import GLOBAL_KEY, project_key
from cold_frame.models import Scope, SearchHit
from cold_frame.observability import get_logger
from cold_frame.store.sqlite import _DB_ERROR, _DB_OPERATIONAL, _connect  # keyed open for import
from cold_frame.write.admission import PII_CATEGORIES

_log = get_logger(__name__)

# any "can't open this DB" error from a keyed open: a driver error (wrong key / corrupt) OR a
# StoreError (missing [crypto] extra). Flattened to a tuple of exception classes for `except`.
_OPEN_ERR: tuple[type[Exception], ...] = (*_DB_ERROR, StoreError)


def _resolve_db(args: argparse.Namespace) -> str:
    return args.db or os.environ.get("COLD_FRAME_DB") or str(branding.DB_PATH)


_OPENED: list[Memory] = []  # memories opened this invocation, closed in main()'s finally


def _memory(args: argparse.Namespace) -> Memory:
    # opt-in PII scrub when --redact-pii is set (only the `add` subcommand exposes the flag)
    pii = PII_CATEGORIES if getattr(args, "redact_pii", False) else None
    mem = Memory(_resolve_db(args), pii_redact=pii)  # offline default: HashEmbedder + llm=None
    _OPENED.append(mem)  # tracked so the connection is closed before the process/command ends
    return mem


def _resolve_id(mem: Memory, prefix: str) -> str | None:
    """Resolve a (possibly 8-char-truncated) id to a full id, or None if absent/ambiguous.

    ``list``/``add`` print 8-char ids, so the CLI accepts a unique prefix. Exact ids
    resolve regardless of status (incl. archived); prefix-matching scans the active set.
    """
    try:
        return mem.get(prefix).id  # exact hit (any status)
    except NoteNotFound:
        matches = [n.id for n in mem.list_active(limit=1_000_000) if n.id.startswith(prefix)]
        return matches[0] if len(matches) == 1 else None


def _cmd_add(args: argparse.Namespace) -> int:
    if not args.text:
        print(f'{PKG}: add requires text (usage: {PKG} add "...")')
        return 1
    # a manual CLI add is a deliberate, cross-project statement → the GLOBAL tier (recalled
    # everywhere), distinct from auto-capture's per-project tagging (D26).
    res = _memory(args).add(args.text, raw=args.raw, scope=Scope(agent_id=GLOBAL_KEY))
    for note in res.added:
        print(f"+ {note.id[:8]}  {note.content}")
    for note in res.held:
        print(f"~ {note.id[:8]}  (held for review)  {note.content}")
    for span in res.redacted:  # content-free: category + count, never the value (I16)
        print(f"  redacted {span.count}x {span.category} before storing")
    # a secret was caught pre-disk — report it (never echo the value, I16)
    for bspan in res.blocked:
        print(f"! blocked {bspan.placeholder} (a secret was detected — not stored)")
    for dup in res.deduped:  # recognized as a restatement of an existing fact (merged, reinforced)
        print(f"= {dup[:8]}  (already known — reinforced)")
    if not (res.added or res.held or res.blocked or res.deduped):
        print("nothing extracted")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    if not args.query:
        print(f"{PKG}: search requires a query")
        return 1
    as_of: datetime | None = None
    if args.as_of:  # rewind: search memory as it was valid on a past date (bi-temporal)
        try:
            parsed = datetime.fromisoformat(args.as_of)
        except ValueError:
            print(f"{PKG}: --as-of must be an ISO date/datetime, e.g. 2026-03-01")
            return 1
        as_of = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    res = _memory(args).search(args.query, k=args.k, as_of=as_of)
    if not res.hits:
        print("no matches")
        return 0
    for hit in res.hits:
        print(f"{hit.score:.4f}  {hit.note.id[:8]}  {hit.note.content}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    mem = _memory(args)
    notes = mem.list_active(limit=args.limit)
    if not notes:
        print("no active memories")
        return 0
    for n in notes:
        band = mem.strength(n.id).band
        print(f"{n.id[:8]}  [{band:9}] {n.content[:NOTE_MAX_CHARS]}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if not args.id:
        print("show: provide a note id")
        return 1
    mem = _memory(args)
    resolved = _resolve_id(mem, args.id)
    if resolved is None:
        print(f"not found (or ambiguous prefix): {args.id}")
        return 1
    n = mem.get(resolved)
    print(f"id:       {n.id}")
    print(f"status:   {n.status}  type: {n.memory_type}  v{n.version}")
    print(f"content:  {n.content}")
    print(f"created:  {n.created_at}  valid_at: {n.valid_at}")
    print(f"conf={n.confidence}  importance={n.importance}  pinned={n.pinned}")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    mem = _memory(args)
    h = mem.health()
    active = mem.list_active(limit=1_000_000)
    by_type = Counter(n.memory_type for n in active)
    print(f"notes={h['notes']}  active={len(active)}  fts={h['fts']}  vec={h['vec']}")
    print("by type: " + (", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "(none)"))
    return 0


def _cmd_timeline(args: argparse.Namespace) -> int:
    """Belief timeline of one note: every persisted version, oldest→newest (fork_history)."""
    if not args.id:
        print("timeline: provide a note id")
        return 1
    mem = _memory(args)
    resolved = _resolve_id(mem, args.id)
    if resolved is None:
        print(f"not found (or ambiguous prefix): {args.id}")
        return 1
    versions = mem.fork_history(resolved)
    if not versions:
        print(f"no history for {resolved[:8]}")
        return 0
    print(f"timeline of {resolved[:8]}  ({len(versions)} version(s)):")
    for v in versions:
        mark = "▶" if v.status == "active" else "·"
        print(f"  {mark} v{v.version}  [{v.status:8}]  valid_at={v.valid_at}  {v.content[:60]}")
    return 0


def _cmd_path(args: argparse.Namespace) -> int:
    """Shortest edge path between two notes (bounded ego-lens BFS, undirected, ≤max-hops)."""
    mem = _memory(args)
    src = _resolve_id(mem, args.src)
    dst = _resolve_id(mem, args.dst)
    if src is None:
        print(f"not found (or ambiguous prefix): {args.src}")
        return 1
    if dst is None:
        print(f"not found (or ambiguous prefix): {args.dst}")
        return 1
    if src == dst:
        print(f"{src[:8]} (same note)")
        return 0
    # BFS over edges treated as undirected; each frontier item carries its edge trail.
    visited: set[str] = {src}
    frontier: list[tuple[str, list[str]]] = [(src, [])]
    for _hop in range(args.max_hops):
        nxt: list[tuple[str, list[str]]] = []
        for node, trail in frontier:
            for edge in mem.neighbors(node):
                other = edge.dst_id if edge.src_id == node else edge.src_id
                if other in visited:
                    continue
                arrow = "->" if edge.src_id == node else "<-"
                step = f"{arrow}[{edge.relation}] {other[:8]}"
                if other == dst:
                    print(f"{src[:8]} " + " ".join([*trail, step]))
                    return 0
                visited.add(other)
                nxt.append((other, [*trail, step]))
        if not nxt:
            break
        frontier = nxt
    print(f"no path within {args.max_hops} hop(s): {src[:8]} … {dst[:8]}")
    return 1


def _cmd_setup(args: argparse.Namespace) -> int:
    """First-run setup: create/migrate the DB and print the Claude Code wiring steps."""
    mem = _memory(args)  # constructing a Memory runs migrate() (idempotent)
    h = mem.health()
    print(f"{PKG}: ready.")
    print(f"  db:       {h['db_path']}")
    print(f"  embedder: {h['embedder_id']} (dim={h['dim']}, offline — no key, no network)")
    print()
    print("Turn on AUTOMATIC memory in Claude Code (recall every session, capture as you work):")
    print(f"  {PKG} hook install                          # wires Claude Code hooks (~/.claude)")
    print(f"  claude mcp add {branding.MCP_ID} -- {PKG} mcp   # drain + tools ([mcp] extra)")
    print()
    print("Or use it directly:")
    print(f'  {PKG} add "I prefer dark roast coffee"')
    print(f'  {PKG} search "coffee"')
    print(f"  {PKG} ui        # browse your memory at {branding.ui_base_url()}")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    """Hard-scrub a secret/PII note from every grain + VACUUM + grep-verify (NOT revivable)."""
    if not args.id:
        print("purge: provide a note id")
        return 1
    if not args.force:  # destructive + irreversible → never on a bare invocation
        print(f"purge permanently scrubs {args.id} (not revivable) — re-run with --force")
        return 1
    mem = _memory(args)
    resolved = _resolve_id(mem, args.id)
    if resolved is None:
        print(f"not found (or ambiguous prefix): {args.id}")
        return 1
    report = mem.purge(resolved, cascade=args.cascade)
    status = "clean" if report.grep_clean else "RESIDUE FOUND"
    print(f"purged {resolved[:8]}: scrubbed {report.rows_scrubbed} row(s), grep={status}")
    return 0 if report.grep_clean else 1


def _cmd_doctor(args: argparse.Namespace) -> int:
    mem = _memory(args)
    h = mem.health()
    pending_caps = mem._store.pending_count("capture")  # auto-capture queue depth (D26)
    dead = mem._store.dead_count()
    oldest_age = mem._store.oldest_pending_age(now=mem._clock.now())
    stale_backlog = oldest_age is not None and oldest_age > 86_400  # jobs not draining for >1 day
    print(f"db: {h['db_path']}")
    print(f"auto-capture: {pending_caps} pending, {dead} dead (drains as you use Coldframe)")
    if (
        stale_backlog
    ):  # the silent-stall signal: oldest_pending_age spans ALL job kinds, not capture
        hrs = int((oldest_age or 0) // 3600)
        print(f"  → jobs not draining for >{hrs}h — run '{PKG} worker'")
    if dead:
        print(f"  → {dead} dead job(s) — check logs; capture/maintenance is failing")
    print(f"notes={h['notes']} fts={h['fts']} vec={h['vec']}  (match={h['counts_match']})")
    print(f"integrity_check={h['integrity']}  fts_integrity={h['fts_integrity']}")
    print(f"embedder={h['embedder_id']} dim={h['dim']}  stale_vectors={h['stale_vectors']}")
    print(f"at_rest_encryption={'on' if h.get('encrypted') else 'off'}")
    if h["stale_vectors"]:  # vectors from an old embedder → not semantically searchable yet
        print(f"  → run '{PKG} reembed' to re-index {h['stale_vectors']} stale vector(s)")
    # report the RESOLVED port the running UI recorded (deep-links use it), not just the default
    resolved_port = branding.UI_PORT
    with contextlib.suppress(OSError, ValueError):
        resolved_port = int(branding.UI_PORTFILE.read_text().strip())
    print(f"ui_port={resolved_port}")
    ok = (
        bool(h["counts_match"])
        and h["integrity"] == "ok"
        and h["fts_integrity"] == "ok"
        and h["stale_vectors"] == 0
        and dead == 0
        and not stale_backlog
    )
    print("ok" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


# ── Claude Code hooks (auto-recall / capture, D26) ───────────────────────────
_RECALL_K = 7  # SessionStart digest: inject up to K strongest durable beliefs
_HOOK_DRAIN_MAX = 3  # Stop hook drains a few naive jobs so capture works without a running worker
_PROMPT_RECALL_K = 3  # UserPromptSubmit: tighter — at most K hits per prompt (avoid per-turn noise)
_PROMPT_MIN_QUERY = 8  # don't query on a trivially short prompt
_PROMPT_SCORE_FLOOR = 0.02  # drop weak fused scores (pure-vector noise sits below a lexical match)


def _settings_path(*, user: bool) -> Path:
    """Claude Code settings file: ~/.claude (user) or ./.claude (project)."""
    base = Path.home() if user else Path.cwd()
    return base / ".claude" / "settings.json"


def _hook_stdin() -> dict[str, object]:
    """The hook's JSON payload (cwd/session_id/transcript_path) — empty on a tty or any error so a
    hook never blocks or crashes."""
    if sys.stdin.isatty():
        return {}
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _cmd_hook_session_start(args: argparse.Namespace) -> int:
    """SessionStart recall hook: emit the strongest durable memories as additionalContext so a new
    Claude Code session opens already knowing the user. Read-only (no reinforce, no write); on ANY
    error it emits nothing and exits 0 — a hook must never block the session (D26)."""
    try:
        cwd = str(_hook_stdin().get("cwd", ""))
        mem = _memory(args)
        # recall this project's memories + the global tier (D26). Rank by COMPUTED strength
        # (importance + retrievability + access), not flat importance; skip the fading band.
        tiers = list(dict.fromkeys([project_key(cwd), GLOBAL_KEY]))  # dedup if outside any project
        notes = [
            n
            for t in tiers
            for n in mem.list_active(
                scope=Scope(agent_id=t), sort="importance", limit=_RECALL_K * 6
            )
        ]
        ranked = sorted(
            ((mem.strength(n.id), n) for n in notes), key=lambda sn: sn[0].value, reverse=True
        )
        lines = [f"- {n.content}" for s, n in ranked if s.band != "fading"][:_RECALL_K]
        if not lines:
            return 0  # silence > noise: nothing worth surfacing
        ctx = (
            "Relevant memory from Coldframe (this user's local memory):\n"
            + "\n".join(lines)
            + f"\n(Full memory: run `{PKG} ui`.)"
        )
        out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}
        print(json.dumps(out))
    except Exception as exc:  # a hook failure must degrade to a silent no-op, never crash a session
        # ...but this read path has no jobs/doctor backstop, so log a content-free breadcrumb (I16)
        # to stderr (stdout carries the hook JSON) — else "Claude forgot me" is undebuggable.
        _log.warning("hook_session_start_failed", extra={"exc_type": type(exc).__name__})
        return 0
    return 0


def _cmd_hook_user_prompt(args: argparse.Namespace) -> int:
    """UserPromptSubmit incremental recall (D26): search this project + global for memories relevant
    to the CURRENT prompt and inject the top few. Tighter + gated vs session-start (a lexical/BM25
    signal AND a score floor) so it adds signal, not per-turn noise. Read-only (reinforce=False);
    fail-silent — a hook must never block the session."""
    try:
        payload = _hook_stdin()
        query = str(payload.get("prompt", "")).strip()
        if len(query) < _PROMPT_MIN_QUERY:
            return 0
        cwd = str(payload.get("cwd", ""))
        mem = _memory(args)
        tiers = list(dict.fromkeys([project_key(cwd), GLOBAL_KEY]))
        best: dict[str, SearchHit] = {}
        for t in tiers:
            for h in mem.search(
                query, scope=Scope(agent_id=t), k=_PROMPT_RECALL_K, reinforce=False
            ).hits:
                # require a lexical (BM25) overlap AND a non-trivial fused score → real relevance,
                # not pure embedding noise that would fire on every unrelated prompt.
                if h.signals.bm25 is None or h.score < _PROMPT_SCORE_FLOOR:
                    continue
                if h.note.id not in best or h.score > best[h.note.id].score:
                    best[h.note.id] = h
        top = sorted(best.values(), key=lambda h: h.score, reverse=True)[:_PROMPT_RECALL_K]
        if not top:
            return 0
        lines = [f"- {h.note.content}" for h in top]
        ctx = "Possibly relevant memory (Coldframe):\n" + "\n".join(lines)
        ev = "UserPromptSubmit"
        out = {"hookSpecificOutput": {"hookEventName": ev, "additionalContext": ctx}}
        print(json.dumps(out))
    except Exception as exc:  # fail-silent; content-free breadcrumb to stderr (I16)
        _log.warning("hook_user_prompt_failed", extra={"exc_type": type(exc).__name__})
        return 0
    return 0


def _hook_present(entries: object, cmd: str) -> bool:
    """True if our hook command is already wired in a settings SessionStart entry list."""
    if not isinstance(entries, list):
        return False
    for e in entries:
        hooks = e.get("hooks", []) if isinstance(e, dict) else []
        if isinstance(hooks, list):
            for h in hooks:
                if isinstance(h, dict) and cmd in str(h.get("command", "")):
                    return True
    return False


# (settings event, matcher, `hook` subcommand) — recall on SessionStart, capture on Stop. Stop fires
# every turn-end, so capture is already continuous (the watermark advances); a PreCompact boundary
# adds nothing here (compaction shrink is handled by the watermark-reset in read_user_messages).
_HOOK_WIRING: tuple[tuple[str, str, str], ...] = (
    ("SessionStart", "startup|resume", "session-start"),
    ("UserPromptSubmit", "", "user-prompt"),
    ("Stop", "", "stop"),
)


# NOTE: agent-push capture (the agent extracts + calls add_memory) ships in the Claude Code PLUGIN
# as a skill (packaging/plugin/skills/remember-facts) — no per-machine CLAUDE.md. `hook install`
# below is the non-plugin fallback: it wires the recall + capture-backstop hooks into settings.json.
def _cmd_hook_install(args: argparse.Namespace) -> int:
    path = _settings_path(user=not args.project)
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        print(f"{PKG}: couldn't read {path} — fix or remove it, then retry")
        return 1
    hooks = settings.setdefault("hooks", {})
    added: list[str] = []
    for event, matcher, sub in _HOOK_WIRING:
        cmd = f"{PKG} hook {sub}"
        entries = hooks.setdefault(event, [])
        if _hook_present(entries, cmd):  # idempotent
            continue
        entries.append({"matcher": matcher, "hooks": [{"type": "command", "command": cmd}]})
        added.append(event)
    if added:
        try:  # a read-only / managed ~/.claude must not greet onboarding with a raw traceback
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"{PKG}: couldn't write {path} ({exc.strerror or exc}) — check permissions")
            return 1
        print(f"installed {', '.join(added)} hook(s) → {path}")
    else:
        print(f"hooks already wired → {path}")
    print("  recall: SessionStart + UserPromptSubmit · capture backstop: Stop (naive, keyless)")
    print("  tip: `claude plugin install coldframe` bundles this + agent-push capture")
    mcp_add = f'claude mcp add {branding.MCP_ID} --env PROJECT_ROOT="$PWD" -- {PKG} mcp'
    print(f"  connect the server: {mcp_add}")
    return 0


def _cmd_hook_uninstall(args: argparse.Namespace) -> int:
    """Remove Coldframe's hooks from Claude Code settings (the memory DB is untouched)."""
    path = _settings_path(user=not args.project)
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):  # don't clobber an unreadable/malformed settings.json with {}
        print(f"{PKG}: couldn't read {path} — fix or remove it, then retry")
        return 1
    hooks = settings.get("hooks", {})
    removed: list[str] = []
    if isinstance(hooks, dict):
        for event, _matcher, sub in _HOOK_WIRING:
            cmd = f"{PKG} hook {sub}"
            entries = hooks.get(event)
            if not isinstance(entries, list):
                continue
            kept = [e for e in entries if not _hook_present([e], cmd)]
            if len(kept) != len(entries):
                removed.append(event)
            hooks[event] = kept
        if settings:
            try:
                path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            except OSError as exc:  # don't report success if the hooks weren't actually removed
                print(f"hook uninstall: couldn't write {path} ({exc.strerror}) — hooks NOT removed")
                return 1
    print(f"removed hooks: {', '.join(removed) or 'none'}")
    print("  (your memory DB is untouched — `cold-frame` still works directly)")
    return 0


def _cmd_hook_status(args: argparse.Namespace) -> int:
    for scope, user in (("user", True), ("project", False)):
        path = _settings_path(user=user)
        try:
            settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            settings = {}
        hooks = settings.get("hooks", {}) if isinstance(settings.get("hooks"), dict) else {}
        wired = [
            event
            for event, _m, sub in _HOOK_WIRING
            if _hook_present(hooks.get(event, []), f"{PKG} hook {sub}")
        ]
        state = ("✓ " + "+".join(wired)) if wired else "— not installed"
        print(f"{scope:8} {path}: {state}")
    print(f"install with: {PKG} hook install   (remove with: {PKG} hook uninstall)")
    return 0


def _cmd_hook_capture(args: argparse.Namespace) -> int:
    """Capture-commit hook (Stop): enqueue the session's transcript span AND drain a few jobs with
    the keyless naive extractor, so capture happens every turn-end without a separately-run
    `cold-frame worker` (D26 B6). naive is fast (HashEmbedder, no model) + bounded; a running worker
    or the agent-push skill still provide the higher-quality path. Only Stop is wired (see
    _HOOK_WIRING). Fast + fail-silent."""
    try:
        payload = _hook_stdin()
        tp = str(payload.get("transcript_path", ""))
        sid = str(payload.get("session_id", ""))
        if tp and sid:
            mem = _memory(args)
            mem.enqueue_capture(tp, sid, str(payload.get("cwd", "")))
            mem.run_pending_jobs(max_jobs=_HOOK_DRAIN_MAX)  # naive drain → no manual worker needed
    except Exception as exc:  # fail-silent: never break the session on a capture hiccup
        _log.warning("hook_capture_failed", extra={"exc_type": type(exc).__name__})  # content-free
        return 0
    return 0


def _cmd_hook_help(args: argparse.Namespace) -> int:
    print(f"usage: {PKG} hook {{session-start|user-prompt|stop|install|uninstall|status}}")
    return 1


def _cmd_mcp(args: argparse.Namespace) -> int:
    os.environ["COLD_FRAME_DB"] = _resolve_db(args)  # forward --db to the server (it reads the env)
    from cold_frame.mcp import main as mcp_main  # lazy: keeps heavy deps out of the CLI

    return mcp_main()


def _cmd_consolidate(args: argparse.Namespace) -> int:
    res = _memory(args).consolidate()  # manual forgetting trigger (SPEC §6); sync
    print(f"consolidate: archived {len(res.archived)}, merged {len(res.merged)}")
    return 0


def _cmd_reembed(args: argparse.Namespace) -> int:
    """Re-index notes under the currently-configured embedder (run after swapping embedders)."""
    res = _memory(args).reembed()
    if res.reembedded:
        print(f"reembed: re-indexed {res.reembedded} note(s) under {res.embedder_id}")
    else:
        print(f"reembed: nothing stale — all vectors current under {res.embedder_id}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    out = Path(args.path)
    out.parent.mkdir(parents=True, exist_ok=True)
    mem = _memory(args)
    if args.events:  # portable append-only event-log dump (I17)
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for line in mem.export_events():
                f.write(line + "\n")
                n += 1
        print(f"exported {n} events → {out}")
    else:  # complete consistent snapshot of the whole DB (I17)
        mem.snapshot(str(out))
        print(f"exported snapshot → {out}")
    return 0


def _import_key() -> str | None:
    """The at-rest key for import/restore (same source as Memory) — an encrypted snapshot must be
    opened keyed to validate/lock it. Blank → None (the open then fails as 'not valid')."""
    return os.environ.get("COLD_FRAME_KEY") or None


def _db_is_busy(path: Path, key: str | None) -> bool:
    """True if another process holds ``path`` open (can't take an exclusive lock immediately).
    Keyed so an encrypted live DB can be lock-probed (else it'd error as 'not a database')."""
    try:
        conn = _connect(str(path), key, timeout=0.0)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("ROLLBACK")
            return False
        finally:
            conn.close()
    except _DB_OPERATIONAL:
        return True  # genuinely locked by another process (I17)
    except _OPEN_ERR:
        # can't even open it (wrong key / corrupt / missing [crypto]) — not a lock. Treat as
        # not-busy so the import (which backs up dst first) proceeds rather than crashing here.
        return False


def _cmd_import(args: argparse.Namespace) -> int:
    """Restore the memory DB from a snapshot (replaces the current DB; current is backed up)."""
    import shutil

    src = Path(args.path)
    dst = Path(_resolve_db(args))
    if not src.exists():
        print(f"import: source not found: {src}")
        return 1
    # keyed so an ENCRYPTED snapshot can be validated (else it reads as ciphertext)
    key = _import_key()
    try:  # validate it's a cold-frame snapshot: (decryptable) SQLite + migrated + notes table
        ro = _connect(str(src), key)
        try:
            version = int(ro.execute("PRAGMA user_version").fetchone()[0])
            has_notes = (
                ro.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='notes'"
                ).fetchone()
                is not None
            )
        finally:
            ro.close()
    except _OPEN_ERR:
        kh = " (wrong $COLD_FRAME_KEY?)" if key else " (encrypted snapshot? set $COLD_FRAME_KEY)"
        print(f"import: {src} is not a valid cold-frame snapshot{kh}")
        return 1
    if version < 1 or not has_notes:
        print(f"import: {src} is not a cold-frame snapshot")
        return 1
    if dst.exists() and _db_is_busy(dst, key):  # I17: never replace a DB another process has open
        print(f"import: {dst} is in use — stop cold-frame (ui/mcp/worker) first, then retry")
        return 1
    try:
        if dst.exists():  # safety-backup the current DB, then drop its stale WAL/SHM
            shutil.copy2(dst, f"{dst}.pre-import.bak")
            for ext in ("-wal", "-shm"):
                stale = Path(f"{dst}{ext}")
                if stale.exists():
                    stale.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        # stage to a temp sibling then os.replace → an ATOMIC swap: dst is never left half-written
        # by a partial copy, and a process still holding dst open keeps reading its old inode (no
        # corruption) until it reopens — this also de-fangs a missed in-use check on a WAL DB.
        tmp = Path(f"{dst}.import.tmp")
        shutil.copy2(src, tmp)
        tmp.replace(dst)  # atomic rename (same fs) — dst is never left half-written
    except OSError as exc:
        Path(f"{dst}.import.tmp").unlink(missing_ok=True)  # drop a partial staged copy
        bak = Path(f"{dst}.pre-import.bak")
        hint = f"; previous DB preserved at {bak}" if bak.exists() else ""
        print(f"import failed: {exc}{hint}")
        return 1
    note = (
        f" (previous DB backed up to {dst}.pre-import.bak)"
        if Path(f"{dst}.pre-import.bak").exists()
        else ""
    )
    print(f"imported {src} → {dst}{note}")
    return 0


def _cmd_worker(args: argparse.Namespace) -> int:
    """Drain the durable jobs queue (consolidation + dead-letter recovery, I12). When the `claude`
    CLI is on PATH, captures extract via the user's Claude session (headless `claude -p` — no API
    key, billed to the subscription); otherwise naive (D26 agent-push alternative)."""
    from cold_frame.llm.claude_cli import ClaudeCliLLM

    extractor = ClaudeCliLLM() if ClaudeCliLLM.available() else None
    mem = Memory(_resolve_db(args), llm=extractor)
    _OPENED.append(mem)
    print(
        f"{PKG} worker: extraction via the claude CLI (session auth, no key)"
        if extractor is not None
        else f"{PKG} worker: claude CLI not found on PATH → naive extraction"
    )
    if args.once:
        ran = mem.run_pending_jobs()
        print(f"worker: ran {ran} job(s)")
        return 0
    import time  # background poller: run pending jobs, sleep, repeat (Ctrl-C to stop)

    if args.interval <= 0:  # 0 → busy-spin; negative → time.sleep raises. Reject up front.
        print(f"{PKG}: --interval must be > 0 (seconds)")
        return 1
    print(f"{PKG} worker: polling every {args.interval}s (Ctrl-C to stop)")
    try:
        while True:
            mem.run_pending_jobs()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def _cmd_jobs(args: argparse.Namespace) -> int:
    """Inspect / recover the background jobs queue (capture + consolidation). `--retry-dead` revives
    dead-lettered jobs so nothing — a failed capture, say — is silently lost forever."""
    mem = _memory(args)
    if args.retry_dead:
        n = mem._store.requeue_dead(now=mem._clock.now())
        print(f"requeued {n} dead job(s) → pending (drains via `{PKG} worker` or normal use)")
        return 0
    print(f"pending={mem._store.pending_count()}  dead={mem._store.dead_count()}")
    if mem._store.dead_count():
        print(f"  recover them: {PKG} jobs --retry-dead")
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    from cold_frame.ui.server import serve  # lazy import (stdlib-only server)

    port = args.port or branding.UI_PORT

    def _ready(resolved: int) -> None:
        print(f"{PKG} ui → {branding.ui_base_url(resolved)}  (Ctrl-C to stop)")

    try:
        serve(_memory(args), port=port, on_ready=_ready)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse tree (one subparser per SPEC §9 subcommand)."""
    parser = argparse.ArgumentParser(prog=PKG, description="local-first memory for LLM agents")
    parser.add_argument("--version", action="version", version=f"{PKG} {__version__}")
    parser.add_argument("--db", help="path to the memory.db (else $COLD_FRAME_DB or the default)")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_add = sub.add_parser("add", help="add a fact or messages")
    p_add.add_argument("text", nargs="?", help="text to remember")
    p_add.add_argument("--raw", action="store_true", help="store verbatim, skip extraction")
    p_add.add_argument(
        "--redact-pii", action="store_true", help="scrub email/phone/card/ssn before storing"
    )
    p_add.set_defaults(func=_cmd_add)

    p_search = sub.add_parser("search", help="search memory")
    p_search.add_argument("query", nargs="?", help="search query")
    p_search.add_argument("-k", type=int, default=10, help="number of hits")
    p_search.add_argument(
        "--as-of", help="rewind: search memory as it was valid on an ISO date (e.g. 2026-03-01)"
    )
    p_search.set_defaults(func=_cmd_search)

    p_list = sub.add_parser("list", help="list active notes")
    p_list.add_argument("--limit", type=int, default=50, help="max notes to list")
    p_list.set_defaults(func=_cmd_list)
    p_show = sub.add_parser("show", help="show one note by id")
    p_show.add_argument("id", nargs="?", help="note id")
    p_show.set_defaults(func=_cmd_show)
    sub.add_parser("stats", help="show store statistics").set_defaults(func=_cmd_stats)
    p_timeline = sub.add_parser("timeline", help="show a note's belief/version timeline")
    p_timeline.add_argument("id", nargs="?", help="note id (or unique prefix)")
    p_timeline.set_defaults(func=_cmd_timeline)
    p_path = sub.add_parser("path", help="show edge path between two notes")
    p_path.add_argument("src", help="source note id (or unique prefix)")
    p_path.add_argument("dst", help="destination note id (or unique prefix)")
    p_path.add_argument("--max-hops", type=int, default=4, help="max edges to traverse")
    p_path.set_defaults(func=_cmd_path)
    sub.add_parser("doctor", help="run install/DB/embedder/invariant checks").set_defaults(
        func=_cmd_doctor
    )
    sub.add_parser("consolidate", help="run forgetting/consolidation now").set_defaults(
        func=_cmd_consolidate
    )
    sub.add_parser("reembed", help="re-index vectors under the current embedder").set_defaults(
        func=_cmd_reembed
    )
    p_export = sub.add_parser("export", help="back up memory (snapshot, or --events NDJSON)")
    p_export.add_argument("path", help="output file path")
    p_export.add_argument("--events", action="store_true", help="dump the event log as NDJSON")
    p_export.set_defaults(func=_cmd_export)
    p_import = sub.add_parser("import", help="restore memory from a snapshot (replaces current)")
    p_import.add_argument("path", help="snapshot file to restore from")
    p_import.set_defaults(func=_cmd_import)
    p_worker = sub.add_parser("worker", help="drain the background jobs queue (maintenance)")
    p_worker.add_argument("--once", action="store_true", help="run one drain pass and exit")
    p_worker.add_argument("--interval", type=float, default=5.0, help="poll interval seconds")
    p_worker.set_defaults(func=_cmd_worker)
    p_jobs = sub.add_parser("jobs", help="inspect/recover the background jobs queue")
    p_jobs.add_argument("--retry-dead", action="store_true", help="revive dead-lettered jobs")
    p_jobs.set_defaults(func=_cmd_jobs)
    p_ui = sub.add_parser("ui", help="launch the local web UI")
    p_ui.add_argument("--port", type=int, default=None, help="UI port (else 27182 + auto-fallback)")
    p_ui.set_defaults(func=_cmd_ui)
    sub.add_parser("mcp", help="run the MCP stdio server").set_defaults(func=_cmd_mcp)
    p_hook = sub.add_parser("hook", help="Claude Code auto-recall/capture hooks (D26)")
    p_hook.set_defaults(func=_cmd_hook_help)  # `hook` with no event → usage
    hook_sub = p_hook.add_subparsers(dest="hook_cmd", metavar="<event>")
    hook_sub.add_parser("session-start", help="emit recall context for a new session").set_defaults(
        func=_cmd_hook_session_start
    )
    hook_sub.add_parser(
        "user-prompt", help="emit recall relevant to the current prompt"
    ).set_defaults(func=_cmd_hook_user_prompt)
    hook_sub.add_parser(
        "stop", help="enqueue the session transcript for auto-capture"
    ).set_defaults(func=_cmd_hook_capture)
    p_hi = hook_sub.add_parser(
        "install", help="wire the recall + capture hooks into Claude Code settings"
    )
    p_hi.add_argument(
        "--project", action="store_true", help="install into ./.claude (else ~/.claude)"
    )
    p_hi.set_defaults(func=_cmd_hook_install)
    p_hu = hook_sub.add_parser("uninstall", help="remove Coldframe's recall + capture hooks")
    p_hu.add_argument("--project", action="store_true", help="from ./.claude (else ~/.claude)")
    p_hu.set_defaults(func=_cmd_hook_uninstall)
    hook_sub.add_parser("status", help="show whether Coldframe's hooks are installed").set_defaults(
        func=_cmd_hook_status
    )
    sub.add_parser("setup", help="first-run setup").set_defaults(func=_cmd_setup)
    p_purge = sub.add_parser("purge", help="hard-scrub a secret/PII note (irreversible)")
    p_purge.add_argument("id", nargs="?", help="note id (or unique prefix)")
    p_purge.add_argument("--force", action="store_true", help="confirm the irreversible scrub")
    p_purge.add_argument("--cascade", action="store_true", help="also purge derived summaries")
    p_purge.set_defaults(func=_cmd_purge)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    func = args.func
    try:
        result: int = func(args)
        return result
    except (
        ColdFrameError
    ) as exc:  # expected failure (bad key / corrupt DB / …) → clean msg, not a trace
        print(f"{PKG}: {exc}", file=sys.stderr)
        return 1
    finally:  # release DB connections (so e.g. `import`'s file-replace isn't held open)
        for mem in _OPENED:
            mem.close()
        _OPENED.clear()


if __name__ == "__main__":
    raise SystemExit(main())
