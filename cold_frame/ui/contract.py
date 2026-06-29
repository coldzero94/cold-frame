"""The UI JSON wire contract — the SINGLE SOURCE OF TRUTH for the cross-language API shapes.

These TypedDicts are exactly what the server emits (cold_frame/ui/server.py) and what the
TS client mirrors. The TS types in ``frontend/src/api.generated.ts`` are GENERATED from these
(``scripts/gen_api_types.py`` → ``frontend/src/api.schema.json`` → json-schema-to-typescript),
so the two languages cannot drift: change a shape here, run ``pnpm -C frontend run gen:types``,
and the TS updates. ``tests/test_api_contract.py`` fails the core gate if the committed schema is
stale, and CI re-runs the generator + ``git diff --exit-code`` to catch a stale ``.generated.ts``.

mypy --strict checks these against the builders; pydantic (a core dep — no new dependency) reads
them at codegen time via ``TypeAdapter(...).json_schema()``.
"""

from __future__ import annotations

from typing import Any, TypedDict, get_args

from pydantic import TypeAdapter

from cold_frame.models import Band, MemoryTypeLiteral, StatusLiteral, TriageReason


class StrengthDict(TypedDict):
    value: float
    band: Band
    at_risk: bool


class NoteBriefDict(TypedDict):
    id: str
    content: str
    memory_type: MemoryTypeLiteral
    status: StatusLiteral
    confidence: float
    strength: StrengthDict


class SourceDict(TypedDict):
    kind: str
    ref: str
    role: str | None
    observed_at: str


class EdgeDict(TypedDict):
    src: str
    dst: str
    relation: str


class FactDetailDict(NoteBriefDict):
    sources: list[SourceDict]
    valid_at: str | None
    edges: list[EdgeDict]
    accesses: list[str]  # recall timestamps (ISO) for the decay sparkline — oldest→newest


class SignalsDict(TypedDict):
    # per-hit retrieval explainability (SPEC §5); optionals are null when that channel didn't fire.
    semantic: float | None
    bm25: float | None
    edge: float | None
    rrf: float
    rerank: float | None


class SearchHitDict(NoteBriefDict):
    score: float
    signals: SignalsDict


class TriageItemDict(NoteBriefDict):
    # a note held for human review (low-confidence / true-conflict / ambiguous-merge).
    reason: TriageReason  # the source domain, not bare str → a named TS union the UI can switch on
    candidates: list[str]
    impact: float


class HistoryVersionDict(TypedDict):
    # one persisted version of a note, oldest→newest — the rewindable belief trail (fork-history).
    id: str
    content: str
    status: StatusLiteral
    version: int
    valid_at: str | None
    invalid_at: str | None


class FieldNoteDict(TypedDict):
    id: str
    content: str
    type: MemoryTypeLiteral
    s: float
    band: Band
    atRisk: bool
    importance: float
    access: int
    pinned: bool
    ageDays: int


# ── response envelopes (what each GET endpoint returns) ──────────────────────
# `total` = count of ALL active notes in scope; `notes` may be a render-capped prefix of that, so
# the client can show "showing N of M" instead of silently dropping the tail.
class NotesResponse(TypedDict):
    notes: list[NoteBriefDict]
    total: int


class MemoryFieldResponse(TypedDict):
    notes: list[FieldNoteDict]
    total: int


class SearchResponse(TypedDict):
    query: str
    hits: list[SearchHitDict]


class FactHistoryResponse(TypedDict):
    versions: list[HistoryVersionDict]


class TriageResponse(TypedDict):
    items: list[TriageItemDict]


# Every wire shape fed to the schema generator — top-level response envelopes plus their nested
# component types (e.g. the fact endpoint returns FactDetailDict | null).
CONTRACT_TYPES = (
    StrengthDict,
    NoteBriefDict,
    SourceDict,
    EdgeDict,
    FactDetailDict,
    FieldNoteDict,
    SignalsDict,
    SearchHitDict,
    TriageItemDict,
    HistoryVersionDict,
    NotesResponse,
    MemoryFieldResponse,
    SearchResponse,
    FactHistoryResponse,
    TriageResponse,
)

# String-literal domains hoisted into named JSON-Schema $defs → named TS unions (Band, …).
# Derived from the models.py Literals (single source) so a new member flows through automatically
# instead of silently falling back to an un-hoisted inline union.
_ENUMS: dict[str, tuple[str, ...]] = {
    "Band": get_args(Band),
    "MemoryType": get_args(MemoryTypeLiteral),
    "Status": get_args(StatusLiteral),
    "TriageReason": get_args(TriageReason),
}
_ENUM_BY_VALUES: dict[frozenset[str], str] = {frozenset(v): k for k, v in _ENUMS.items()}
_ENUM_DEFS: dict[str, list[str]] = {k: list(v) for k, v in _ENUMS.items()}


def _transform(node: object, refmap: dict[str, str], used: set[str]) -> object:
    """One pass: drop noisy ``title``s, hoist inline string-enums to a named ``$ref``,
    and rewrite ``$ref`` targets through ``refmap`` (Dict-suffixed → clean names)."""
    if isinstance(node, dict):
        if node.get("type") == "string" and isinstance(node.get("enum"), list):
            name = _ENUM_BY_VALUES.get(frozenset(node["enum"]))
            if name:
                used.add(name)
                return {"$ref": f"#/$defs/{name}"}
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "title":  # pydantic stamps a title on every field/def → drop (json2ts noise)
                continue
            if k == "$ref" and isinstance(v, str) and v.startswith("#/$defs/"):
                old = v[len("#/$defs/") :]
                out[k] = f"#/$defs/{refmap.get(old, old)}"
            else:
                out[k] = _transform(v, refmap, used)
        return out
    if isinstance(node, list):
        return [_transform(x, refmap, used) for x in node]
    return node


def build_api_schema() -> dict[str, Any]:
    """The combined JSON Schema for the whole UI wire contract — the language-neutral artifact
    the TS client is generated from. Clean type names (no ``Dict`` suffix) + named string-enum
    types. Deterministic: ``frontend/src/api.schema.json`` must equal this (test_api_contract)."""
    refmap = {t.__name__: t.__name__.removesuffix("Dict") for t in CONTRACT_TYPES}
    raw: dict[str, Any] = {}
    for t in CONTRACT_TYPES:
        schema = TypeAdapter(t).json_schema(ref_template="#/$defs/{model}")
        for name, sub in schema.pop("$defs", {}).items():
            raw[name] = sub  # nested types (e.g. StrengthDict) referenced via $ref
        raw[t.__name__] = schema
        refmap.setdefault(t.__name__, t.__name__.removesuffix("Dict"))

    used_enums: set[str] = set()
    defs: dict[str, Any] = {}
    for name, sub in raw.items():
        defs[refmap.get(name, name)] = _transform(sub, refmap, used_enums)
    for enum_name in sorted(used_enums):  # add the hoisted enum defs
        defs[enum_name] = {"type": "string", "enum": _ENUM_DEFS[enum_name]}

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ColdframeApiContract",
        "$defs": dict(sorted(defs.items())),  # sorted → stable diffs
    }
