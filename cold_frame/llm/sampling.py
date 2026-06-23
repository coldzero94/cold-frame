"""SamplingLLM — an ``LLM`` that rides on an injected text-completion callback.

The parasitic strategy: when cold-frame runs *inside* a host agent (Claude Code) there is no
own LLM key/endpoint — internal judgments (dedup/conflict/consolidate/procedural) ride on the
HOST's model via MCP sampling. This class is the host-agnostic core seam for that: it takes a
sync ``Sampler`` callback ``(system, user) -> text`` and parses the host's reply into the
requested schema. The MCP bridge that fulfils the callback lives in ``cold_frame/mcp.py`` —
this module stays pure (no mcp/anyio import, I9).

ANY sampler failure (raised, empty, non-JSON, schema-invalid) degrades to ``parsed=None``, so
every caller behaves exactly as offline (``llm=None``): the deterministic engine decides. The
host model is treated as REMOTE (``is_local=False``) so a secret/PII tiebreak never rides on it
(I7) — it falls closed instead.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from cold_frame.llm.base import LLM, LLMResult, TaskTag
from cold_frame.observability import get_logger

_log = get_logger(__name__)

# (system, user) -> assistant text. Raising or returning "" means "no completion" → degrade.
Sampler = Callable[[str, str], str]

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> str | None:
    """Best-effort: strip a ```json fence, then take the outermost ``{...}``. None if absent."""
    fenced = _JSON_FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    return candidate[start : end + 1]


class SamplingLLM(LLM):
    """An LLM backed by a sync text-completion callback (e.g. host MCP sampling). Degrades safe."""

    def __init__(self, sampler: Sampler, *, name: str = "mcp:host", is_local: bool = False) -> None:
        self._sample = sampler
        self.name = name
        self._is_local = is_local

    @property
    def is_local(self) -> bool:
        return self._is_local  # host model = remote by default → excluded from secret tiebreak (I7)

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
        try:
            text = self._sample(system, user)
        except Exception:
            _log.warning("sampling_failed", extra={"task": task.value})
            return LLMResult(text="", parsed=None, model=self.name)
        if not text:
            _log.info("sampling_empty", extra={"task": task.value})  # host declined/unsupported
            return LLMResult(text="", parsed=None, model=self.name)
        if schema is None:
            return LLMResult(text=text, parsed=None, model=self.name)
        raw = _extract_json(text)
        parsed: BaseModel | None = None
        if raw is not None:
            try:
                parsed = schema.model_validate_json(raw)
            except ValidationError:
                _log.info("sampling_unparseable", extra={"task": task.value})  # → deterministic
        return LLMResult(text=text, parsed=parsed, model=self.name)
