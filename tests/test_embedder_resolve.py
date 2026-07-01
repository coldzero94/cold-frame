"""Opt-in embedder selection via $COLD_FRAME_EMBEDDER (audit ADD #1).

HashEmbedder stays the offline I5 default; "local" opts into the [local-llm] semantic embedder.
The CLI/MCP surfaces read the env and pass it to Memory (which itself stays embedder-param-only).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from cold_frame.cli import main
from cold_frame.exceptions import ColdFrameError
from cold_frame.llm import HashEmbedder, resolve_embedder

_HAS_ST = importlib.util.find_spec("sentence_transformers") is not None


def test_resolve_defaults_to_hash() -> None:
    assert isinstance(resolve_embedder(None), HashEmbedder)  # unset → offline default (I5)
    assert isinstance(resolve_embedder("hash"), HashEmbedder)
    assert isinstance(resolve_embedder("  Hash  "), HashEmbedder)  # case/space-insensitive


def test_resolve_unknown_name_raises_cleanly() -> None:
    with pytest.raises(ColdFrameError, match="unknown"):
        resolve_embedder("gpt-4o")


def test_resolve_local_needs_the_extra_or_returns_the_embedder() -> None:
    if _HAS_ST:
        emb = resolve_embedder("local")
        assert emb.meta.embedder_id.startswith("local:") and emb.is_local
    else:  # [local-llm] absent → a clean, actionable error (not a raw ImportError trace)
        with pytest.raises(ColdFrameError, match="local-llm"):
            resolve_embedder("local")


def test_cli_hash_embedder_env_is_the_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLD_FRAME_EMBEDDER", "hash")
    assert main(["--db", str(tmp_path / "m.db"), "add", "dark roast coffee"]) == 0


def test_cli_bad_embedder_env_exits_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COLD_FRAME_EMBEDDER", "gpt-4o")
    assert main(["--db", str(tmp_path / "m.db"), "add", "hi"]) == 1  # clean exit, not a traceback
    assert "unknown" in capsys.readouterr().err.lower()
