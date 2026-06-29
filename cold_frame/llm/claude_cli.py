"""``ClaudeCliLLM`` — borrow the user's Claude session via the ``claude`` CLI (no API key).

Claude Code does NOT support MCP sampling (#1785), so Coldframe can't pull the host model that way.
But the ``claude`` CLI is already installed + logged in, and its headless ``-p`` mode runs a
one-shot completion against the user's session (verified: works with ``ANTHROPIC_API_KEY`` unset →
billed to the subscription, not the API). So the auto-capture drain extracts/classifies by shelling
out to
``claude -p`` — deterministic, keyless, no per-machine CLAUDE.md, no agent cooperation.

Isolation (no recursion): the nested call passes ``--setting-sources ""`` (don't load the user's
hooks / CLAUDE.md) + ``--strict-mcp-config`` (no MCP servers) + ``COLD_FRAME_EXTRACTING=1``, so it
can't re-trigger Coldframe's own hooks. Stdlib only (subprocess + shutil + json) — no new dep, no
extra (I9). Any failure (claude absent, timeout, nonzero, unparseable) → ``parsed=None`` so the
engine degrades to the deterministic naive path, exactly as offline.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Final

from pydantic import BaseModel

from cold_frame.llm.base import LLM, LLMResult, TaskTag
from cold_frame.observability import get_logger

_log = get_logger(__name__)

# A fast, cheap model is right for batch extraction/classification (not the session's default opus).
_DEFAULT_MODEL: Final[str] = "claude-haiku-4-5-20251001"


def _result_text(stdout: str) -> str:
    """Pull the model's text out of ``claude -p --output-format json``'s envelope."""
    try:
        env = json.loads(stdout)
    except ValueError:
        return ""
    return str(env.get("result", "")) if isinstance(env, dict) else ""


def _parse_json_object(text: str) -> dict[str, object] | None:
    """Best-effort parse of the first JSON object in ``text`` (tolerate ``` fences / prose)."""
    s = text.strip()
    if s.startswith("```"):  # ```json … ``` fence
        s = s.strip("`")
        s = s[4:] if s.lower().startswith("json") else s
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


class ClaudeCliLLM(LLM):
    """LLM seam backed by the headless ``claude`` CLI (the user's session — no API key)."""

    name = "claude-cli"

    def __init__(
        self, *, model: str = _DEFAULT_MODEL, timeout: float = 90.0, binary: str = "claude"
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._binary = binary

    @property
    def is_local(self) -> bool:
        return (
            False  # the cloud model via the session — NOT local (admission tasks stay fail-closed)
        )

    @staticmethod
    def available(binary: str = "claude") -> bool:
        """True if the ``claude`` CLI is on PATH (so the drain can borrow the session)."""
        return shutil.which(binary) is not None

    def complete(
        self,
        *,
        task: TaskTag,
        system: str,
        user: str,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> LLMResult:
        argv = [
            self._binary,
            "-p",
            "--output-format",
            "json",
            "--system-prompt",
            system,
            "--model",
            self._model,
            "--setting-sources",
            "",  # isolation: no user/project hooks or CLAUDE.md (no recursion)
            "--strict-mcp-config",  # isolation: no MCP servers loaded
            user,
        ]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",  # the `claude` CLI emits UTF-8; don't depend on the system locale
                errors="replace",  # tolerate stray bytes rather than raising UnicodeDecodeError
                timeout=self._timeout,
                # spread FIRST so the forced guard value always wins even if it's already in the env
                env={**_os_environ(), "COLD_FRAME_EXTRACTING": "1"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning(
                "claude_cli_failed", extra={"exc_type": type(exc).__name__, "task": task.value}
            )
            return LLMResult(model=self.name)
        if proc.returncode != 0:
            _log.warning("claude_cli_nonzero", extra={"task": task.value, "code": proc.returncode})
            return LLMResult(model=self.name)
        text = _result_text(proc.stdout)
        parsed: BaseModel | None = None
        if schema is not None and text:
            obj = _parse_json_object(text)
            if obj is not None:
                try:
                    parsed = schema.model_validate(obj)
                except Exception:  # model returned the wrong shape → degrade to deterministic
                    parsed = None
        return LLMResult(text=text, parsed=parsed, model=self.name)


# Vars stripped from the `claude` child env:
# - COLD_FRAME_KEY: our at-rest master key must NEVER cross into a third-party subprocess (I16 /
#   trust boundary — readable via /proc/<pid>/environ or the child's crash telemetry).
# - ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN: ClaudeCliLLM is documented to use the user's SESSION/
#   subscription ("the Claude you already pay for — no extra key or cost"). Forwarding an API key
#   would silently make every auto-capture a METERED API call. Strip them → `claude -p` uses session
#   auth (no session → it fails and the keyless deterministic backstop covers capture).
_SECRET_ENV_VARS = ("COLD_FRAME_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _os_environ() -> dict[str, str]:
    import os

    # COLD_FRAME_DB (a path, not a secret) is left for the child to inherit.
    env = dict(os.environ)
    for var in _SECRET_ENV_VARS:
        env.pop(var, None)
    return env
