"""Golden-set harness — the integration backbone (eval §B; CLAUDE.md §2).

Engine correctness is proven by deterministic mock-LLM golden cases, not hand-poking.
The schema is the doc-canonical §B.1 shape (``suite:`` / ``Step.op`` / ``llm_script``
list / ``expect`` block). Each case runs against a fresh in-memory ``Memory`` wired
with HashEmbedder + a frozen, step-advanced clock + uuid5 ids (G6 determinism); the
LLM, when a case declares one, is a stateful ``ScriptedLLM`` whose unmatched call is a
hard ``EvalError`` — an undeclared LLM interaction never silently passes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from cold_frame.api import Memory
from cold_frame.exceptions import ColdFrameError
from cold_frame.llm.base import LLM, Embedder, HashEmbedder, LLMResult, TaskTag
from cold_frame.models import EdgeRelation, MemoryTypeLiteral, Note, Scope, StatusLiteral

# Fixed namespace so eval ids are uuid5(NS, f"{case.id}:{ordinal}") — stable snapshots.
_EVAL_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "cold-frame.eval")
_DEFAULT_INSTANT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


class EvalError(ColdFrameError):
    """A golden case failed: an assertion mismatched, or an LLM call was unscripted."""


# ── golden-set schema (eval §B.1, doc-canonical) ──────────────────────────────
class LlmScriptEntry(BaseModel):
    """One scripted LLM response, matched against the rendered user prompt."""

    task: TaskTag
    match: dict[str, Any] = Field(default_factory=dict)  # {contains:str}|{seq:int}|{any:true}
    returns: dict[str, Any] = Field(default_factory=dict)  # parsed into the call's schema


class Step(BaseModel):
    """One action in a case timeline. ``at`` advances the injected clock (G6)."""

    op: Literal["add", "search", "consolidate", "correct", "tick", "forget", "revive", "pin"]
    at: datetime | None = None
    scope: Scope | None = None
    text: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ExpectNote(BaseModel):
    """Assertion on a note created during the case (looked up by ``where``)."""

    where: dict[str, Any]  # {content_like|id|content}
    status: StatusLiteral | None = None
    memory_type: MemoryTypeLiteral | None = None
    invalid_at: datetime | None = None
    held_for_human: bool | None = None
    quarantined: bool | None = None
    version: int | None = None


class ExpectEdge(BaseModel):
    relation: EdgeRelation
    src_like: str | None = None
    dst_like: str | None = None


class ExpectSearch(BaseModel):
    """Assertion on a search run after the steps."""

    query: str
    scope: Scope | None = None
    as_of: datetime | None = None
    k: int = 10
    token_budget: int | None = None  # pass a token budget to the packer (§5.8)
    expect_top_content_like: str | None = None
    expect_contains: list[str] | None = None  # each substring must appear in some top-k hit
    expect_count: int | None = None  # e.g. 0 for the cross-scope leak guard
    expect_used_le: int | None = None  # SearchResult.used_tokens must be <= this (budget cap)


class ExpectBlock(BaseModel):
    notes: list[ExpectNote] = Field(default_factory=list)
    edges: list[ExpectEdge] = Field(default_factory=list)
    search: list[ExpectSearch] = Field(default_factory=list)


class Case(BaseModel):
    id: str
    description: str = ""
    seed: int = 0
    embedder: str = "hash"
    llm_script: list[LlmScriptEntry] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    expect: ExpectBlock = Field(default_factory=ExpectBlock)


class Suite(BaseModel):
    suite: str
    embedder: str = "hash"
    cases: list[Case] = Field(default_factory=list)


class CaseReport(BaseModel):
    case_id: str
    passed: bool
    failures: list[str] = Field(default_factory=list)


class SuiteReport(BaseModel):
    suite: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    cases: list[CaseReport] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.failed == 0


# ── deterministic mock LLM (eval §B.2): stateful, ordered-with-fallback, sync (I4) ──
class ScriptedLLM(LLM):
    """Replays scripted structured responses; an unmatched call is a hard EvalError.

    Specific matches (``contains``/``seq``) are consumed once; ``any:true`` entries are
    reusable, lowest priority. This forces a case to declare every LLM interaction.
    """

    name = "mock"

    def __init__(self, script: list[LlmScriptEntry], *, is_local: bool = True) -> None:
        self._script = list(script)
        self._consumed = [False] * len(self._script)
        self._is_local = is_local
        self._seq = 0
        self.calls: list[dict[str, str]] = []

    @property
    def is_local(self) -> bool:
        return self._is_local

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
        self._seq += 1
        self.calls.append({"task": task.value, "user": user})
        entry = self._pick(task, user, self._seq)
        if entry is None:
            raise EvalError(f"no scripted response for task={task.value} user={user[:80]!r}")
        if schema is not None:
            return LLMResult(parsed=schema.model_validate(entry.returns), model="mock")
        return LLMResult(text=str(entry.returns.get("text", "")), model="mock")

    def _pick(self, task: TaskTag, user: str, seq: int) -> LlmScriptEntry | None:
        fallback: LlmScriptEntry | None = None
        for i, e in enumerate(self._script):
            if e.task != task:
                continue
            if e.match.get("any"):
                fallback = fallback or e
                continue
            if self._consumed[i]:
                continue
            if "contains" in e.match and str(e.match["contains"]) in user:
                self._consumed[i] = True
                return e
            if "seq" in e.match and int(e.match["seq"]) == seq:
                self._consumed[i] = True
                return e
        return fallback


# ── deterministic clock + id factory (G6) ─────────────────────────────────────
class _EvalClock:
    """A Clock pinned to a settable instant; the runner advances it per step.at."""

    def __init__(self, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

    def set(self, instant: datetime) -> None:
        self._instant = instant


def _id_factory(case_id: str) -> Callable[[], str]:
    counter = {"i": -1}

    def factory() -> str:
        counter["i"] += 1
        return str(uuid.uuid5(_EVAL_NS, f"{case_id}:{counter['i']}"))

    return factory


def _embedder_for(name: str) -> Embedder:
    if name == "hash":
        return HashEmbedder()
    raise EvalError(f"unsupported eval embedder {name!r} (P1 supports 'hash' only)")


# ── loader ────────────────────────────────────────────────────────────────────
def load_suite(path: str | Path) -> Suite:
    """Load + validate a golden-set Suite from a YAML file (raises EvalError if absent)."""
    p = Path(path)
    if not p.is_file():
        raise EvalError(f"suite file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise EvalError(f"suite YAML must be a mapping, got {type(data).__name__}: {p}")
    return Suite.model_validate(data)


# ── runner + assertions ───────────────────────────────────────────────────────
def _first_instant(case: Case) -> datetime:
    for step in case.steps:
        if step.at is not None:
            return step.at
    return _DEFAULT_INSTANT


def _match_where(note: Note, where: dict[str, Any]) -> bool:
    if "id" in where:
        return note.id == str(where["id"])
    if "content" in where:
        return note.content == str(where["content"])
    if "content_like" in where:
        return str(where["content_like"]).lower() in note.content.lower()
    return False


def run_case(case: Case, *, via_tool: bool = False) -> CaseReport:
    """Run one golden case against a fresh in-memory Memory; collect assertion failures.

    ``via_tool`` routes every ``add`` op through the ``create_fact`` self-edit tool instead of
    ``Memory.add`` — same WriteCore, so dedup/freshness suites must produce identical outcomes
    (the P6 I15 gate).
    """
    failures: list[str] = []
    clock = _EvalClock(_first_instant(case))
    llm = ScriptedLLM(case.llm_script) if case.llm_script else None
    mem = Memory(
        ":memory:",
        embedder=_embedder_for(case.embedder),
        llm=llm,
        clock=clock,
        id_factory=_id_factory(case.id),
    )
    created: list[str] = []
    try:
        for step in case.steps:
            if step.at is not None:
                clock.set(step.at)
            if step.op == "add":
                if step.text is None:
                    failures.append("add step missing text")
                    continue
                if via_tool:  # I15 gate: the agent asserts the fact via the self-edit tool
                    res = mem.create_fact(step.text, scope=step.scope or Scope())
                else:
                    res = mem.add(
                        step.text,
                        scope=step.scope or Scope(),
                        observed_at=clock.now(),
                        raw=bool(
                            step.extra.get("raw", False)
                        ),  # raw → naive extract (script only dedup/conflict)
                    )
                created.extend(n.id for n in res.added)
                created.extend(n.id for n in res.held)
            elif step.op == "search":
                mem.search(
                    step.text or str(step.extra.get("query", "")), scope=step.scope or Scope()
                )
            elif step.op == "consolidate":
                mem.consolidate(scope=step.scope or Scope(), caps=step.extra.get("caps"))
            elif step.op in ("pin", "forget", "revive"):
                target = _find_created(mem, created, step)
                if target is None:
                    failures.append(f"{step.op} step: no created note matches {step.extra!r}")
                else:
                    getattr(mem, step.op)(target)
            else:
                failures.append(f"op {step.op!r} not supported")

        notes = mem._store.get_notes(created)
        for en in case.expect.notes:
            failures.extend(_check_note(en, notes))
        for ee in case.expect.edges:
            failures.extend(_check_edges(ee, notes, mem))
        for es in case.expect.search:
            failures.extend(_check_search(es, mem))
    finally:
        mem._store.close()
    return CaseReport(case_id=case.id, passed=not failures, failures=failures)


def _find_created(mem: Memory, created: list[str], step: Step) -> str | None:
    """Resolve a pin/forget/revive target among created notes by extra.content_like|id."""
    nid = step.extra.get("id")
    if nid is not None:
        return str(nid) if str(nid) in created else None
    like = step.extra.get("content_like")
    if like is None:
        return None
    for note in mem._store.get_notes(created):
        if str(like).lower() in note.content.lower():
            return note.id
    return None


def _check_edges(ee: ExpectEdge, notes: list[Note], mem: Memory) -> list[str]:
    src = next((n for n in notes if ee.src_like and ee.src_like.lower() in n.content.lower()), None)
    dst = next((n for n in notes if ee.dst_like and ee.dst_like.lower() in n.content.lower()), None)
    if src is None or dst is None:
        return [f"edge {ee.relation}: note(s) not found (src~{ee.src_like!r}, dst~{ee.dst_like!r})"]
    edges = mem._store.neighbors([src.id], relations=[ee.relation])
    if not any(e.src_id == src.id and e.dst_id == dst.id for e in edges):
        return [f"edge {ee.relation} {ee.src_like!r}→{ee.dst_like!r} not found"]
    return []


def _check_note(en: ExpectNote, notes: list[Note]) -> list[str]:
    match = next((n for n in notes if _match_where(n, en.where)), None)
    if match is None:
        return [f"expected note {en.where} not found"]
    out: list[str] = []
    checks: list[tuple[str, Any, Any]] = [
        ("status", en.status, match.status),
        ("memory_type", en.memory_type, match.memory_type),
        ("invalid_at", en.invalid_at, match.invalid_at),
        ("held_for_human", en.held_for_human, match.held_for_human),
        ("quarantined", en.quarantined, match.quarantined),
        ("version", en.version, match.version),
    ]
    for field, want, got in checks:
        if want is not None and got != want:
            out.append(f"note {en.where} {field}: got {got!r}, want {want!r}")
    return out


def _check_search(es: ExpectSearch, mem: Memory) -> list[str]:
    res = mem.search(
        es.query, scope=es.scope or Scope(), k=es.k, as_of=es.as_of, token_budget=es.token_budget
    )
    hits = res.hits
    out: list[str] = []
    if es.expect_count is not None and len(hits) != es.expect_count:
        out.append(f"search {es.query!r}: got {len(hits)} hits, want {es.expect_count}")
    if es.expect_used_le is not None:
        used = res.used_tokens or 0
        if used > es.expect_used_le:
            out.append(f"search {es.query!r}: used {used} tokens > budget {es.expect_used_le}")
    if es.expect_top_content_like is not None:
        if not hits:
            out.append(f"search {es.query!r}: no hits, want top ~ {es.expect_top_content_like!r}")
        elif es.expect_top_content_like.lower() not in hits[0].note.content.lower():
            out.append(
                f"search {es.query!r}: top {hits[0].note.content!r} !~ "
                f"{es.expect_top_content_like!r}"
            )
    if es.expect_contains is not None:
        contents = [h.note.content.lower() for h in hits]
        for sub in es.expect_contains:
            if not any(sub.lower() in c for c in contents):
                out.append(f"search {es.query!r}: top-{es.k} missing {sub!r}")
    return out


def run_suite(suite_or_path: Suite | str | Path) -> SuiteReport:
    """Run every case in a suite; SuiteReport.ok ⇔ all cases passed."""
    suite = suite_or_path if isinstance(suite_or_path, Suite) else load_suite(suite_or_path)
    reports = [run_case(c) for c in suite.cases]
    passed = sum(1 for r in reports if r.passed)
    total = len(reports)
    return SuiteReport(
        suite=suite.suite,
        total=total,
        passed=passed,
        failed=total - passed,
        cases=reports,
        metrics={"pass_rate": (passed / total) if total else 1.0},
    )
