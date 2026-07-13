"""Claude Code transcript reader + Layer-A salience pre-filter (auto-capture, D26).

The hook enqueues a transcript POINTER; the drain calls this to read only the NEW user-message text
since the watermark. Layer-A is the cheap, deterministic, zero-LLM front line of the anti-bloat
design: keep the user's stated facts/decisions/corrections, drop assistant output, tool_use/
tool_result noise, trivially short turns, oversized pastes, and (in _is_durable_user_fact) questions
and imperative task-requests — so the LLM extractor + durability gate downstream only ever see a
small, salient slice. Stdlib only (json + pathlib + hashlib); no heavy deps (I9).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cold_frame.api import Msg  # type-only — a runtime import would cycle

# ── git-based project tag + global/project tiering (D26 project scoping) ──────
GLOBAL_KEY = "global"  # the scope (agent_id) tier for cross-project facts (recalled everywhere)
# project facts from a session with no resolvable cwd → isolated bucket, NOT the global tier (a
# project fact must never leak cross-project just because cwd was unknown, D26).
LOCAL_KEY = "proj:unknown"

# Clear personal identity/preference leads → a GLOBAL fact (recalled in every project).
# Conservative: anything else stays project-scoped, so a project fact can't leak to other repos.
_GLOBAL_LEADS = (
    "i prefer ",
    "i like ",
    "i love ",
    "i hate ",
    "i am a ",
    "i'm a ",
    "my name",
    "call me ",
    "i live ",
    "i work at ",
    "my email",
    "my phone",
    "i'm allergic",
    "i am allergic",
    "i always ",
    "i usually ",
    "i never ",
)


def _git_remote(config_text: str) -> str | None:
    """Pull remote.origin.url out of a .git/config (stable across clone location / path)."""
    in_origin = False
    for line in config_text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_origin = s.replace(" ", "").replace('"', "").lower() == "[remoteorigin]"
        elif in_origin and s.lower().startswith("url") and "=" in s:
            return s.split("=", 1)[1].strip()  # guard: a malformed 'url' line w/o '=' is skipped
    return None


def _read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _git_dir_from_pointer(gitfile: Path) -> Path | None:
    """A linked worktree/submodule has ``.git`` as a FILE: ``gitdir: <path>`` → resolve it."""
    text = _read_text(gitfile)
    for line in (text or "").splitlines():
        if line.startswith("gitdir:"):
            p = Path(line.split(":", 1)[1].strip())
            return p if p.is_absolute() else (gitfile.parent / p).resolve()
    return None


def _remote_from_git_dir(gitdir: Path) -> str | None:
    """remote.origin.url for a git dir, following a worktree's ``commondir`` to the shared repo."""
    cfg = _read_text(gitdir / "config")  # submodules have their own config+remote
    if cfg and (remote := _git_remote(cfg)):
        return remote
    common = _read_text(gitdir / "commondir")  # a linked worktree's gitdir has no config of its own
    if common:
        rel = common.strip()
        common_dir = Path(rel) if Path(rel).is_absolute() else (gitdir / rel).resolve()
        shared = _read_text(common_dir / "config")
        if shared:
            return _git_remote(shared)
    return None


def _project_basis(cwd: str) -> str:
    """The stable identity of the project at ``cwd``: git remote URL → git repo root → cwd.

    Resolves the remote even when ``.git`` is a FILE (a linked worktree or submodule) so a worktree
    and its main checkout share ONE project_key instead of being keyed by divergent checkout paths.
    """
    d = Path(cwd)
    for parent in (d, *d.parents):
        gitdir = parent / ".git"
        if gitdir.exists():
            real = gitdir if gitdir.is_dir() else _git_dir_from_pointer(gitdir)
            if real is not None and (remote := _remote_from_git_dir(real)):
                return remote
            return str(parent)  # a git repo without an origin remote → the repo root path
    return str(d)  # not a git repo → the working directory itself


def project_key(cwd: str | None) -> str:
    """A stable per-project scope tag from cwd (git-based, hybrid). Empty cwd → an ISOLATED local
    bucket (LOCAL_KEY), NOT the global tier — else a project fact captured with no cwd would leak
    cross-project. Genuinely-global facts are routed by the tier classifier, not by this."""
    if not cwd:
        return LOCAL_KEY
    return "proj:" + hashlib.blake2b(_project_basis(cwd).encode("utf-8"), digest_size=8).hexdigest()


def is_global_fact(text: str) -> bool:
    """Route a captured fact to the GLOBAL tier (cross-project) vs the current project.
    Conservative: only clear personal identity/preferences are global; the rest stays local."""
    return text.strip().lower().startswith(_GLOBAL_LEADS)


_MIN_CHARS = 12  # drop trivially short user turns ("ok", "yes", "thanks", "go on")
_MAX_CHARS = (
    4000  # skip pasted blobs (logs/files/stack traces) — almost never a stated durable fact
)

# Leading words that mark a turn as a task-REQUEST (imperative), not a durable fact about the user.
# Deterministic durability heuristic for the offline path, which (unlike the LLM extractor) has no
# durability gate — without this, "run the tests again" gets stored as a permanent "user fact".
_COMMAND_VERBS = frozenset(
    {
        "run",
        "show",
        "fix",
        "check",
        "look",
        "give",
        "tell",
        "explain",
        "find",
        "open",
        "write",
        "create",
        "make",
        "add",
        "list",
        "search",
        "read",
        "edit",
        "remove",
        "update",
        "change",
        "refactor",
        "implement",
        "generate",
        "test",
        "build",
        "commit",
        "push",
        "install",
        "rename",
        "move",
        "delete",
        "print",
        "help",
        "review",
        "try",
        "use",
        "go",  # "go ahead" (usually after a stripped affirmation) — imperative, not the language
        "proceed",
    }
)

# Leading affirmations. Stripped before the imperative check so "yes go ahead" is judged on "go
# ahead" (dropped), while "yes I moved to Berlin" is judged on "I moved …" (kept — a real fact).
_AFFIRMATIONS = frozenset({"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "no", "nope", "nah"})

# Shell/tool binaries. A turn LEADING with one of these plus a command shape (a CLI flag or a
# command-verb subcommand) and NO copula is a command invocation ("git commit -m 'wip'", "npm
# install express"), not a durable fact. A fact ABOUT a tool has a copula ("git is my VCS") and is
# kept by the declarative guard, so this can't false-drop it.
_SHELL_BINARIES = frozenset(
    {
        "git",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "pip",
        "pip3",
        "uv",
        "uvx",
        "docker",
        "kubectl",
        "cargo",
        "make",
        "pytest",
        "gh",
        "brew",
        "curl",
        "wget",
        "ssh",
        "grep",
        "sed",
        "awk",
        "cd",
        "ls",
        "rm",
        "cp",
        "mv",
        "cat",
        "mkdir",
        "touch",
        "chmod",
        "export",
        "source",
        "bash",
    }
)
_REQUEST_PREFIXES = (
    "can you",
    "could you",
    "would you",
    "please ",
    "let's ",
    "lets ",
    "how do",
    "what",
)

# Copula/modal markers that flag a command-verb-leading turn as a DECLARATIVE statement, not an
# imperative — so a homograph fact ("test coverage must exceed 80%") survives the leading-verb drop.
_DECLARATIVE_MARKERS = (
    " is ",
    " are ",
    " was ",
    " were ",
    " must ",
    " should ",
    " will ",
    " needs ",
    " need ",
    " has ",
    " have ",
    "'s ",
)


# Harness-injected blocks that arrive as type=user but are NOT user-stated facts: slash commands,
# bash tool I/O, task notifications, interrupt markers. Live dogfooding on a real transcript showed
# these leaking through (`<command-name>/effort</command-name>`, `<bash-input>code .`, …) — pure
# noise/bloat. A turn containing any of these markers is dropped.
_HARNESS_MARKERS = (
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "<task-notification>",
    "[request interrupted",
)


def _is_durable_user_fact(text: str) -> bool:
    """Layer-A salience: keep declarative first-person-ish statements; drop questions, imperatives/
    task-requests, harness/slash/bash noise, trivially short turns, and oversized pastes.
    Heuristic + deterministic. NOTE: the imperative filter is English-only — a non-English task
    request can slip through to the naive backstop; the agent-push path (the agent extracts) is the
    quality answer for non-English, and dedup absorbs the overlap (D26)."""
    if not (_MIN_CHARS <= len(text) <= _MAX_CHARS):
        return False
    t = text.strip().lower()
    if t.endswith("?") or t.startswith(_REQUEST_PREFIXES):
        return False
    if any(marker in t for marker in _HARNESS_MARKERS):
        return False
    declarative = any(marker in t for marker in _DECLARATIVE_MARKERS)
    words = t.split()
    # a shell-command invocation ("git commit -m ...", "npm install express") is not a durable fact:
    # a leading tool binary + a command shape (a CLI flag or a command-verb subcommand), no copula.
    if words and words[0] in _SHELL_BINARIES and not declarative:
        cmd_shape = any(w.startswith("-") for w in words[1:]) or (
            len(words) > 1 and words[1] in _COMMAND_VERBS
        )
        if cmd_shape:
            return False
    # strip a leading affirmation ("yes go ahead") so the imperative check sees the actual head word
    head = 1 if len(words) > 1 and words[0].rstrip(",.!") in _AFFIRMATIONS else 0
    first = words[head] if len(words) > head else ""
    if first not in _COMMAND_VERBS:
        return True
    # a leading command verb is usually an imperative ("run the tests") → drop. BUT many are
    # noun/verb HOMOGRAPHS (test/build/review/change/search/...) that lead real declarative facts —
    # "test coverage must exceed 80%", "review is mandatory". Keep it when a copula/modal marks it
    # a statement, not a command (a rare false-keep is absorbed by dedup downstream).
    return declarative


def _user_text(message: object) -> str:
    """Plain text of a user message, skipping tool_result blocks (which arrive under role=user)."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(p for p in parts if p).strip()
    return ""


def read_user_messages(transcript_path: str | Path, since_line: int = 0) -> tuple[list[Msg], int]:
    """Read NEW user-message text from a Claude Code transcript JSONL after line ``since_line``.

    Returns ``(messages, new_line_count)``; the count is the watermark for the next read so capture
    is incremental + idempotent. CRITICAL: if the file is now SHORTER than the watermark (Claude
    Code compacted/rotated the transcript), reset to a full re-scan — else every post-compaction
    fact is silently lost forever. DEDUP + Layer-B collapse any carry-over, so a re-scan is safe.
    Layer-A keeps only durable user-stated facts (see _is_durable_user_fact); never raises.
    """
    p = Path(transcript_path)
    if not p.is_file():
        return [], since_line
    try:
        # errors="replace": a non-UTF-8 byte must not raise (the "never raises" contract); a salient
        # fact rarely lives in the corrupt bytes. OSError (TOCTOU/permission) → no new facts, with
        # the watermark unchanged so the next drain retries rather than skipping the span.
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], since_line
    # split on the JSONL delimiter ONLY (not splitlines(), which also breaks on U+2028/U+2029/U+0085
    # that can legally appear inside a JSON string) and drop the trailing fragment: a partial,
    # mid-write last line is NOT counted, so the watermark never advances past it and it's re-read
    # once complete (else a finished turn is permanently dropped).
    lines = text.split("\n")[:-1]
    total = len(lines)
    start = 0 if total < since_line else since_line  # shrink/rotation → re-scan the whole file
    msgs: list[Msg] = []
    for raw in lines[start:]:
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            continue  # a partial/corrupt line — skip, keep scanning
        if not isinstance(ev, dict) or ev.get("type") != "user":
            continue  # assistant / tool_result / metadata lines are not user-stated facts
        text = _user_text(ev.get("message"))
        if _is_durable_user_fact(text):
            msgs.append({"role": "user", "content": text})
    return msgs, total
