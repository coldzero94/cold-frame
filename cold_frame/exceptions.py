"""Exception hierarchy (CLAUDE.md §4) + the MCP error-code map.

One hierarchy: ``ColdFrameError`` is the root; every adapter/driver failure is wrapped
in ``StoreError``. The MCP error→code map is 1:1 with these classes, pinned here so the
``prompts/mcp.py`` adapter never invents codes.
"""

from __future__ import annotations

from typing import Final


class ColdFrameError(Exception):
    """Root of every cold-frame error. Catch this at the MCP/CLI boundary."""


class NoteNotFound(ColdFrameError):
    """get/correct/update referenced an unknown note id."""


class EmbedderMismatchError(ColdFrameError):
    """Configured embedder (id/dim) != the dim stored in DB meta (cross-tier guard)."""


class SecretBlocked(ColdFrameError):
    """A secret in the new text of a supersede/update (the explicit self-edit path, which returns
    a single Note). The ``add``/``create_fact`` path instead reports it in ``AddResult.blocked``."""


class VarHealerError(ColdFrameError):
    """A procedural f-string variable was dropped during a gradient edit (SPEC §7 hard-fail)."""


class StoreError(ColdFrameError):
    """Adapter-level failure (txn rollback, migration, driver exception) — wraps the cause."""


class PolicyError(ColdFrameError):
    """A local-only policy was violated (non-local LLM for a secret-span eval, I7). Raised by
    ``llm.assert_local_for``; that guard is DEFERRED with admission/I7 (D25) so nothing in v1
    dispatches it yet — it's tested and ready for when admission lands (v1.1/hosted)."""


class ToolError(ColdFrameError):
    """A self-edit tool call was malformed (unknown tool, missing/empty required arg)."""


# ── MCP error→code map (api-contract §7): 1:1 with the classes above ──
# Unmapped ColdFrameError subclasses → "internal"; unexpected exceptions → "internal".
MCP_ERROR_CODES: Final[dict[type[ColdFrameError], str]] = {
    NoteNotFound: "not_found",
    EmbedderMismatchError: "internal",
    SecretBlocked: "invalid_scope",  # user-actionable (a secret was blocked), not an internal error
    VarHealerError: "internal",
    StoreError: "internal",
    PolicyError: "invalid_scope",
    ToolError: "invalid_scope",
    ColdFrameError: "internal",
}


def mcp_code_for(exc: BaseException) -> str:
    """Map an exception to its stable MCP error code (api-contract §7.6)."""
    if isinstance(exc, ColdFrameError):
        for cls in type(exc).__mro__:
            code = MCP_ERROR_CODES.get(cls)
            if code is not None:
                return code
    return "internal"
