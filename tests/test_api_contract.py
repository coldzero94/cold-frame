"""The committed UI API schema must match the Python wire contract.

This is the no-Node freshness guard in the core gate: if someone edits cold_frame/ui/contract.py
(or the models it projects) without regenerating, the committed frontend/src/api.schema.json goes
stale and this fails — telling them to run `pnpm -C frontend run gen:types`, which also regenerates
the TS client (api.generated.ts) from the same schema, so the Python and TS sides can't drift.
"""

from __future__ import annotations

import json
from pathlib import Path

from cold_frame.ui.contract import CONTRACT_TYPES, build_api_schema

_SCHEMA = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api.schema.json"


def test_committed_api_schema_matches_contract() -> None:
    current = build_api_schema()
    committed = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    assert committed == current, (
        "frontend/src/api.schema.json is stale — run `pnpm -C frontend run gen:types` "
        "(regenerates the schema AND the TS client) and commit the result."
    )


def test_schema_covers_every_contract_type() -> None:
    # every endpoint shape is generated → a named TS type exists for it (no hand-maintained mirror)
    defs = set(build_api_schema()["$defs"])
    for t in CONTRACT_TYPES:
        assert t.__name__.removesuffix("Dict") in defs
    assert {"Band", "MemoryType", "Status"} <= defs  # the hoisted string-literal unions
