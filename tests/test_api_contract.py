"""The committed UI API schema must match the Python wire contract.

This is the no-Node freshness guard in the core gate: if someone edits cold_frame/ui/contract.py
(or the models it projects) without regenerating, the committed frontend/src/api.schema.json goes
stale and this fails — telling them to run `pnpm -C frontend run gen:types`, which also regenerates
the TS client (api.generated.ts) from the same schema, so the Python and TS sides can't drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, get_args, get_origin, get_type_hints

from cold_frame.ui.contract import CONTRACT_TYPES, build_api_schema


def _literal_domains(ann: object) -> list[frozenset[object]]:
    """Every Literal value-set reachable in a field annotation (incl. inside Union/list)."""
    if get_origin(ann) is Literal:
        return [frozenset(get_args(ann))]
    domains: list[frozenset[object]] = []
    for arg in get_args(ann):
        domains.extend(_literal_domains(arg))
    return domains

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


def test_every_contract_literal_is_hoisted() -> None:
    # a Literal field that isn't registered would silently render as an inline (un-named) TS union;
    # fail here so a new Literal is hoisted to a named type.
    from cold_frame.ui.contract import _ENUM_BY_VALUES

    for t in CONTRACT_TYPES:
        for field, ann in get_type_hints(t).items():
            for domain in _literal_domains(ann):
                assert domain in _ENUM_BY_VALUES, (
                    f"{t.__name__}.{field}: Literal {sorted(map(str, domain))} is not hoisted — "
                    f"add its type to contract._ENUMS so it generates a named TS union."
                )
