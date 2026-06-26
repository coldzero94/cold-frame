"""``Memory`` facade (api-contract §2) — the single public Python entrypoint.

This is the canonical SIGNATURE surface (G6 bakes the Clock/id-factory into __init__).
Fully implemented: P1-P6, the local read surface (history/triage/timeline), secret/PII
hard-purge, and the embedder-swap re-embedding migration. Deferred per D25: automatic
admission (REDACT/CONSENT) + its local-only tiebreak (I7).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Literal, TypedDict, cast, get_args

from cold_frame.branding import DB_PATH
from cold_frame.constants import CONSOLIDATE_EVERY_N_WRITES, DEDUP_AUTO_MERGE
from cold_frame.exceptions import EmbedderMismatchError, NoteNotFound, ToolError
from cold_frame.forget.consolidate import Consolidator
from cold_frame.forget.worker import Worker
from cold_frame.integrations.claude_code import (
    GLOBAL_KEY,
    is_global_fact,
    project_key,
    read_user_messages,
)
from cold_frame.llm.base import LLM, Clock, Embedder, HashEmbedder, SystemClock, TaskTag
from cold_frame.models import (
    AddResult,
    ConsolidateResult,
    CorrectResult,
    Edge,
    EdgeRelation,
    MemoryTypeLiteral,
    Note,
    ProceduralResult,
    ReembedResult,
    Scope,
    SearchResult,
    Source,
    SourceKind,
    Strength,
    ToolSpec,
    TriageItem,
)
from cold_frame.observability import get_logger
from cold_frame.procedural.optimize import ProceduralOptimizer
from cold_frame.prompts.scope import SCOPE_SYSTEM, ScopeVerdict, build_scope_user
from cold_frame.read.retrieve import RetrievePipeline
from cold_frame.read.strength import compute_strength
from cold_frame.store.base import Job, PurgeReport
from cold_frame.store.sqlite import SQLiteStore
from cold_frame.write.core import WriteCore
from cold_frame.write.extract import extract

__all__ = ["Memory", "Msg"]

_MEMORY_TYPES: frozenset[str] = frozenset(get_args(MemoryTypeLiteral))  # single source for the enum
_log = get_logger(__name__)


class Msg(TypedDict):
    """A chat message handed to ``Memory.add`` (api-contract §2.1)."""

    role: str
    content: str


class Memory:
    """The single public Python entrypoint (api-contract §2). All methods are sync (I4)."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        embedder: Embedder | None = None,
        llm: LLM | None = None,
        default_scope: Scope | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
        config: object | None = None,
        consolidate_every: int | None = None,
    ) -> None:
        # Open Store, run migrate() (idempotent), assert the configured embedder's dim
        # matches DB meta else raise EmbedderMismatchError. Clock + id-factory injected (G6):
        # default offline = HashEmbedder + llm=None + SystemClock + uuid4 ids (I5).
        self._db_path = db_path or str(DB_PATH)
        self._embedder: Embedder = embedder or HashEmbedder()
        self._llm = llm
        self._default_scope = default_scope or Scope()
        self._clock: Clock = clock or SystemClock()
        self._new_id: Callable[[], str] = id_factory or (lambda: uuid.uuid4().hex)
        self._config = config

        self._store = SQLiteStore(
            self._db_path, embedder=self._embedder, clock=self._clock, new_id=self._new_id
        )
        self._store.migrate()
        stored = self._store.embedder_meta()
        current = self._embedder.meta
        # A genuine corruption risk is the SAME embedder id at a DIFFERENT dim (KNN would vstack
        # mixed-dim vectors). A different id is a legitimate swap — allowed; its vectors are stale
        # (KNN excludes them, I10) until ``reembed`` re-indexes them, so we do NOT raise.
        if (
            stored is not None
            and stored.embedder_id == current.embedder_id
            and stored.dim != current.dim
        ):
            raise EmbedderMismatchError(
                f"embedder {current.embedder_id!r} dim {current.dim} != DB meta dim {stored.dim} "
                f"(same id, different dim — incompatible vectors)"
            )
        self._write = WriteCore(
            self._store, embedder=self._embedder, llm=self._llm, clock=self._clock
        )
        self._read = RetrievePipeline(
            self._store, embedder=self._embedder, llm=self._llm, clock=self._clock
        )
        self._consolidator = Consolidator(
            self._store,
            embedder=self._embedder,
            llm=self._llm,
            clock=self._clock,
            new_id=self._new_id,
        )
        self._procedural = ProceduralOptimizer(
            self._store,
            embedder=self._embedder,
            llm=self._llm,
            clock=self._clock,
            new_id=self._new_id,
            scope=self._default_scope,
        )
        # auto-maintenance (I13): every N new-fact writes, a debounced consolidate job runs —
        # episodic roll-up + decay/cap archive — so the active set stays bounded without the
        # user ever calling consolidate. Durable queue (survives a crash mid-roll-up).
        self._consolidate_every = consolidate_every or CONSOLIDATE_EVERY_N_WRITES
        self._writes_since_consolidate = 0
        # instance-unique worker id (via the injected id-factory, deterministic in tests) so the
        # jobs locked_by fence actually distinguishes workers across processes (I12)
        self._worker = Worker(
            self._store, clock=self._clock, worker_id=f"worker-{self._new_id()[:8]}"
        )
        self._job_handlers: dict[str, Callable[[Job], None]] = {
            "consolidate": self._run_consolidate_job,
            "capture": self._run_capture_job,  # auto-capture from a Claude Code transcript (D26)
        }

    # ── write ────────────────────────────────────────────────────────────
    def add(
        self,
        messages: list[Msg] | str,
        *,
        scope: Scope | None = None,
        infer: bool = True,
        observed_at: datetime | None = None,
        source: Source | None = None,
        raw: bool = False,
    ) -> AddResult:
        scope = scope or self._default_scope
        observed_at = observed_at or self._clock.now()
        candidates = extract(
            messages,
            llm=self._llm,
            clock=self._clock,
            new_id=self._new_id,
            observed_at=observed_at,
            scope=scope,
            source=source,
            infer=infer,
            raw=raw,
        )
        result = self._write.commit(candidates, scope=scope, source=source)
        self._after_write(scope, len(result.added))
        return result

    # ── auto-maintenance: debounced consolidate every N new-fact writes (I13) ──
    def _after_write(self, scope: Scope, n_added: int) -> None:
        if n_added <= 0:
            return
        self._writes_since_consolidate += n_added
        if self._writes_since_consolidate < self._consolidate_every:
            return
        self._writes_since_consolidate = 0
        # Best-effort: the fact is ALREADY committed — a maintenance hiccup (enqueue/drain) must
        # never surface as a failed write. Swallow + log content-free; the durable queue retries.
        try:
            self._store.enqueue(
                "consolidate",
                {"scope": scope.model_dump()},
                dedup_key=f"consolidate:{scope.user_id}:{scope.agent_id}:{scope.session_id}",
            )
            self.run_pending_jobs(max_jobs=5)  # bounded inline drain; consolidate is convergent
        except Exception as exc:
            _log.warning("auto_consolidate_failed", extra={"exc_type": type(exc).__name__})

    def run_pending_jobs(self, *, max_jobs: int = 50) -> int:
        """Lease + run up to ``max_jobs`` due jobs; returns how many ran. Recovers backed-off/
        stale-leased jobs the inline write-path drain may miss — the `cold-frame worker` loop and
        long-running servers call this so maintenance/dead-letter actually progresses (I12)."""
        count = 0
        while count < max_jobs and self._worker.run_once(self._job_handlers):
            count += 1
        return count

    def _run_consolidate_job(self, job: Job) -> None:
        scope = Scope(**job.payload.get("scope", {}))
        self._consolidator.consolidate(scope=scope)  # idempotent/convergent (at-least-once safe)

    # ── auto-capture (D26): enqueue a transcript pointer; drain extracts via THIS Memory's llm ──
    def enqueue_capture(self, transcript_path: str, session_id: str, cwd: str = "") -> None:
        """Queue a Claude Code transcript span for auto-capture (debounced per session). ``cwd`` is
        carried so the drain can tag captures with the git-based project scope (D26). The hook calls
        this — fast, no extraction; the drain (where an LLM is reachable) does the work."""
        self._store.enqueue(
            "capture",
            {"transcript_path": transcript_path, "session_id": session_id, "cwd": cwd},
            dedup_key=f"capture:{session_id}",
        )

    def _novel_messages(self, msgs: list[Msg], scope: Scope) -> list[Msg]:
        """Layer-B novelty pre-filter (D26): drop a turn already represented by a near-identical
        active note (cosine ≥ DEDUP_AUTO_MERGE) so we skip paying to extract known content. A
        multi-fact turn dilutes below the threshold (so it survives); only near-pure restatements
        are dropped — which DEDUP would collapse anyway, just more expensively."""
        out: list[Msg] = []
        known: list[str] = []
        for m in msgs:
            emb = self._embedder.embed_one(m["content"])
            hits = self._store.knn(emb, 1, scope=scope, statuses=["active"])
            if hits and hits[0][1] >= DEDUP_AUTO_MERGE:
                known.append(hits[0][0])  # restatement → reinforce here (it never reaches commit)
                continue
            out.append(m)
        if known:  # Layer-B drops before WriteCore, so reinforce repetition here too (dogfood fix)
            self._store.reinforce(known, now=self._clock.now())
        return out

    def _classify_tiers(self, texts: list[str]) -> list[bool]:
        """Per-text tier — True=global, False=project. Uses the (host-via-sampling / local) LLM when
        present: the same parasitic model the dedup/conflict judges use classifies better than the
        heuristic. Falls back to is_global_fact offline or on a malformed/length-mismatch reply."""
        if self._llm is None or not texts:
            return [is_global_fact(t) for t in texts]
        try:
            parsed = self._llm.complete(
                task=TaskTag.SCOPE_CLASSIFY,
                system=SCOPE_SYSTEM,
                user=build_scope_user(texts),
                schema=ScopeVerdict,
            ).parsed
        except Exception:  # any LLM/bridge failure → degrade to the deterministic heuristic
            parsed = None
        if isinstance(parsed, ScopeVerdict) and len(parsed.tiers) == len(texts):
            return [t == "global" for t in parsed.tiers]
        return [is_global_fact(t) for t in texts]

    def _run_capture_job(self, job: Job) -> None:
        """Read NEW user messages since the watermark → Layer-A (in the reader) → Layer-B novelty →
        the ONE WriteCore via add(infer=True): ADMISSION→DEDUP→CONFLICT→PERSIST + durability gate
        keep the DB lean (D26). Watermark advances only after add() so a crash re-presents a span
        that DEDUP collapses."""
        sid = str(job.payload.get("session_id", ""))
        path = str(job.payload.get("transcript_path", ""))
        pkey = project_key(str(job.payload.get("cwd", "")))  # git-based project tag (D26)
        wkey = f"hook:watermark:{sid}"
        since = int(self._store.get_meta(wkey) or "0")
        msgs, new_line = read_user_messages(path, since)
        # route each turn: personal facts → the GLOBAL tier (recalled everywhere); the rest → this
        # project's tag (isolated). The host/local LLM classifies (heuristic fallback); the tier
        # rides the scope's agent_id (no schema change).
        tiers = self._classify_tiers([m["content"] for m in msgs])
        by_tier: dict[str, list[Msg]] = {}
        for m, is_global in zip(msgs, tiers, strict=True):
            by_tier.setdefault(GLOBAL_KEY if is_global else pkey, []).append(m)
        for tier, tier_msgs in by_tier.items():
            scope = Scope(agent_id=tier)
            fresh = self._novel_messages(tier_msgs, scope)  # Layer-B novelty WITHIN the tier
            if fresh:
                self.add(
                    fresh, infer=True, scope=scope
                )  # source=None → per-message provenance (I14)
        self._store.set_meta(wkey, str(new_line))

    def _supersede_text(
        self,
        id: str,
        new_text: str,
        *,
        reason: str,
        ref: str,
        kind: SourceKind = "manual",
        scope: Scope | None = None,
    ) -> CorrectResult:
        """Replace ``id`` with a new fact carrying ``new_text`` via the one supersede commit.

        Keyed by an EXPLICIT id (not a similarity search) — correct_memory and the
        update_fact/supersede self-edit tools all funnel through here (I15).
        """
        old_notes = self._store.get_notes([id])
        if not old_notes:
            raise NoteNotFound(id)
        old = old_notes[0]
        now = self._clock.now()
        new = Note(
            id=self._new_id(),
            content=new_text,
            memory_type=old.memory_type,
            scope=scope or old.scope,
            created_at=now,
            valid_at=now,  # the replacement is true as of now
            importance=old.importance,
            sources=[
                Source(
                    kind=kind,
                    ref=ref,
                    content_hash=hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
                    observed_at=now,
                ),
                *old.sources,
            ],
        )
        committed = self._write.commit_supersede(id, new, reason=reason)
        return CorrectResult(archived=id, new=committed)

    def correct_memory(
        self, id: str, new_text: str, *, scope: Scope | None = None
    ) -> CorrectResult:
        return self._supersede_text(
            id, new_text, reason="manual correction", ref="correct_memory", scope=scope
        )

    _UPDATABLE: frozenset[str] = frozenset({"importance", "keywords", "tags", "context", "pinned"})

    def update(self, id: str, **fields: object) -> Note:
        """Patch metadata fields (importance/keywords/tags/context/pinned). Content edits go
        through ``update_fact``/``correct_memory`` (bi-temporal supersede); status via forget."""
        bad = set(fields) - self._UPDATABLE
        if bad:
            raise ValueError(
                f"update: unsupported field(s) {sorted(bad)} — use update_fact/forget for those"
            )
        old = self.get(id)  # raises NoteNotFound
        updated = old.model_copy(update={**fields, "version": old.version + 1})
        self._store.update_note(updated, update_type="manual")  # CAS on version; content unchanged
        return self.get(id)

    def delete(self, id: str, *, force: bool = False) -> None:
        """Permanently remove a note + its searchable grains (NOT revivable). Requires
        ``force=True`` — use ``forget`` to archive (revivable) instead. The local append-only
        event log KEEPS prior payloads (an audit trail); to also scrub the content out of the
        event log + VACUUM the free-list, use ``purge`` (the secret/PII carve-out)."""
        if not force:
            raise ValueError(
                "delete() permanently removes a note — pass force=True, or use forget() to archive"
            )
        self._store.delete(id)

    def purge(self, id: str, *, cascade: bool = False) -> PurgeReport:
        """Secret/PII hard-purge — the ONE append-only carve-out (I2/I17/§7). Scrubs the note
        out of EVERY grain incl. the event-log payload, VACUUMs the free-list, then grep-verifies
        the plaintext is gone from the live ``.db``/``.db-wal`` (honest scope: live files only,
        not OS snapshots/backups). ``cascade=True`` also purges notes derived FROM this one so a
        secret can't survive in a summary. NOT revivable. Returns a ``PurgeReport`` proof."""
        return self._store.purge(id, cascade=cascade)

    def pin(self, id: str) -> Note:
        self._store.set_pinned(id, True)  # exempt from decay/archive (I13)
        return self.get(id)

    def forget(self, id: str) -> Note:
        self._store.archive(id, now=self._clock.now())  # archive-not-delete (I2), event co-written
        return self.get(id)

    def revive(self, id: str) -> Note:
        self._store.revive(id)  # un-archive: clears invalid_at/expired_at, event co-written
        return self.get(id)

    # ── read ─────────────────────────────────────────────────────────────
    def search(
        self,
        query: str,
        *,
        scope: Scope | None = None,
        k: int = 10,
        token_budget: int | None = None,
        as_of: datetime | None = None,
        include_archived: bool = False,
        rerank: bool = False,
        reinforce: bool = True,
    ) -> SearchResult:
        return self._read.search(
            query,
            scope=scope or self._default_scope,
            k=k,
            token_budget=token_budget,
            as_of=as_of,
            include_archived=include_archived,
            rerank=rerank,
            reinforce=reinforce,
        )

    def get(self, id: str) -> Note:
        notes = self._store.get_notes([id])
        if not notes:
            raise NoteNotFound(id)
        return notes[0]

    def close(self) -> None:
        """Release the underlying Store connection(s)."""
        self._store.close()

    def health(self) -> dict[str, object]:
        """Doctor/health snapshot: invariant counts + integrity + embedder (eval §C.8)."""
        return self._store.doctor()

    # ── backup / portability (I17: snapshot or event-log dump; never the live WAL) ──
    def snapshot(self, dst: str) -> None:
        """Write a complete consistent backup of the whole memory DB to ``dst``."""
        self._store.snapshot(dst)

    def export_events(self) -> Iterator[str]:
        """Yield the append-only event log as NDJSON lines (portable, inspectable)."""
        for ev in self._store.iter_events():
            yield ev.model_dump_json()

    def get_many(self, ids: list[str]) -> list[Note]:
        return self._store.get_notes(ids)

    def strength(self, id: str) -> Strength:
        return compute_strength(self.get(id), self._clock.now())

    def list_active(
        self,
        *,
        scope: Scope | None = None,
        sort: Literal["decay", "recent", "importance"] = "recent",
        limit: int = 200,
    ) -> list[Note]:
        """Active notes for the inspector/UI (the 'what I know about you now' list)."""
        return self._store.by_status(
            scope=scope or self._default_scope, status="active", sort=sort, limit=limit
        )

    def neighbors(
        self, id: str, *, relations: list[EdgeRelation] | None = None, hops: int = 1
    ) -> list[Edge]:
        return self._store.neighbors([id], relations=relations)  # 1-hop (multi-hop later)

    def fork_history(self, id: str) -> list[Note]:
        """Every persisted version of ``id`` (oldest→newest) — the rewindable belief trail."""
        history = self._store.get_history(id)
        if not history and not self._store.get_notes([id]):
            raise NoteNotFound(id)
        return history

    def access_history(self, id: str) -> list[datetime]:
        """Recall timestamps for ``id`` (oldest→newest) from the capped access_log — the decay
        signal made visible (each recall reinforces; gaps are where forgetting sets in)."""
        return self._store.access_log(id)

    def reembed(self) -> ReembedResult:
        """Re-index every note whose vector was written by a different embedder than the one now
        configured (I8/I10). Run this after swapping the embedder (e.g. installing a local model)
        so the migrated notes are semantically searchable again — until then KNN excludes their
        stale vectors and they degrade to BM25-only. Idempotent: with nothing stale it only
        fast-forwards the stored embedder_meta to the live embedder (no rewrite)."""
        current = self._embedder.meta
        stale = self._store.stale_vector_notes(current_id=current.embedder_id)
        # stale_vector_notes preserves order and embed() is order-preserving → vectors[i] aligns
        # with stale[i]. embed([]) is empty-safe, and Store.reembed handles the empty input as a
        # meta-sync-only no-op (one txn).
        vectors = self._embedder.embed([n.content for n in stale])
        self._store.reembed([(n.id, vectors[i]) for i, n in enumerate(stale)], meta=current)
        return ReembedResult(reembedded=len(stale), embedder_id=current.embedder_id)

    # ── maintenance / forgetting ─────────────────────────────────────────
    def consolidate(
        self,
        *,
        scope: Scope | None = None,
        now: datetime | None = None,
        caps: dict[str, int] | None = None,
    ) -> ConsolidateResult:
        return self._consolidator.consolidate(
            scope=scope or self._default_scope, now=now, caps=caps
        )

    def triage_queue(self, *, scope: Scope | None = None, limit: int = 50) -> list[TriageItem]:
        """Notes held for human review (low-confidence / true-conflict / ambiguous-merge),
        ranked by importance. A null ``triage_reason`` is surfaced as ``low_confidence``."""
        held = self._store.held_for_human(scope=scope or self._default_scope, limit=limit)
        return [
            TriageItem(
                note=n,
                reason=n.triage_reason or "low_confidence",
                candidates=[],
                impact=n.importance,
            )
            for n in held
        ]

    def resolve_triage(
        self,
        id: str,
        action: Literal["pin", "let_go", "merge", "keep", "supersede"],
        *,
        target: str | None = None,
    ) -> None:
        if action in ("merge", "supersede") and target is None:
            raise ValueError(f"resolve_triage: action {action!r} requires a target")
        if action == "supersede":  # the held note wins over `target` — do the failure-prone
            self.forget(target)  # type: ignore[arg-type]  # archive FIRST: a bad target raises
            # before we clear the hold, so no partial resolve (held note stays held).
        # the precondition (if any) passed → accept the held note off the queue. The lifecycle
        # flags held/quarantined/triage_reason live ONLY on the notes row (no fts/vec grain), so
        # this single setter is the whole "clear" — see set_held_for_human.
        self._store.set_held_for_human(id, held=False, quarantined=False, reason=None)
        if action == "pin":  # accept + pin (exempt from decay/archive, I13)
            self._store.set_pinned(id, True)
        elif action in ("let_go", "merge"):
            # let_go: not worth keeping. merge: a duplicate of `target` (presence-checked only).
            # Either way archive the held note (revivable, I2); v1 keeps merge lightweight —
            # no provenance graft into `target` yet. (keep/supersede: the clear above is all.)
            self.forget(id)

    # ── self-edit / procedural ───────────────────────────────────────────
    def memory_tools(self, scope: Scope) -> list[ToolSpec]:
        """The self-edit tools an agent may call (api-contract §2.4). All converge on the
        one WriteCore (I15): create_fact→commit, update_fact/supersede→commit_supersede."""
        return [
            ToolSpec(
                name="create_fact",
                description="Assert a new fact (runs dedup; conflict resolution + freshness "
                "when an LLM is configured).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "memory_type": {"type": "string", "enum": sorted(_MEMORY_TYPES)},
                    },
                    "required": ["text"],
                },
            ),
            ToolSpec(
                name="update_fact",
                description="Correct the fact at id with new text; the old version is archived "
                "(revivable) and the new one supersedes it.",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                },
            ),
            ToolSpec(
                name="supersede",
                description="Supersede the fact at id with a new fact (old archived, revivable).",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                },
            ),
            ToolSpec(
                name="forget",
                description="Archive the fact at id (non-destructive, revivable).",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            ),
        ]

    def create_fact(
        self,
        text: str,
        *,
        scope: Scope | None = None,
        memory_type: MemoryTypeLiteral = "semantic",
        importance: float = 0.5,
    ) -> AddResult:
        """Agent asserts a fact → the SAME WriteCore.commit as add (dedup + conflict, I15).

        ``confidence`` is intentionally left at the model default (1.0): an agent self-asserting
        a fact is high-confidence, distinct from passive extraction's 0.5 — so confidence/
        quarantine-gated cases are out of scope for the via_tool gate.
        """
        scope = scope or self._default_scope
        now = self._clock.now()
        cand = Note(
            id=self._new_id(),
            content=text,
            memory_type=memory_type,
            scope=scope,
            created_at=now,
            valid_at=now,
            importance=importance,
            sources=[
                Source(
                    kind="tool",
                    ref="create_fact",
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    observed_at=now,
                )
            ],
        )
        result = self._write.commit([cand], scope=scope, source=None)
        self._after_write(scope, len(result.added))
        return result

    def update_fact(self, id: str, new_text: str, *, scope: Scope | None = None) -> CorrectResult:
        return self._supersede_text(
            id, new_text, reason="agent update", ref="update_fact", kind="tool", scope=scope
        )

    def supersede(self, id: str, new_text: str, *, scope: Scope | None = None) -> CorrectResult:
        return self._supersede_text(
            id, new_text, reason="agent supersede", ref="supersede", kind="tool", scope=scope
        )

    def apply_tool(
        self, name: str, args: dict[str, object], *, scope: Scope | None = None
    ) -> dict[str, object]:
        """Execute one self-edit tool by name (the MCP/agent entry); routes via WriteCore (I15).

        Every argument-boundary failure raises ``ToolError`` (a ColdFrameError) so the MCP
        layer maps it to a stable error code — never a bare KeyError/ValueError.
        """

        def _require(key: str) -> str:
            val = args.get(key)
            if not isinstance(val, str) or not val:
                raise ToolError(f"self-edit tool {name!r} requires a non-empty {key!r}")
            return val

        if name == "create_fact":
            mt = args.get("memory_type", "semantic")
            if mt not in _MEMORY_TYPES:
                raise ToolError(
                    f"invalid memory_type {mt!r} (expected one of {sorted(_MEMORY_TYPES)})"
                )
            res = self.create_fact(
                _require("text"), scope=scope, memory_type=cast(MemoryTypeLiteral, mt)
            )
            return {
                "added": [n.id for n in res.added],
                "deduped": res.deduped,
                "superseded": res.superseded,
                "held": [n.id for n in res.held],  # durability-gated, agent must see it
                "blocked": [b.reason for b in res.blocked],  # secret BLOCKed pre-disk (I6)
            }
        if name == "update_fact":
            r = self.update_fact(_require("id"), _require("text"), scope=scope)
            return {"archived": r.archived, "new": r.new.id}
        if name == "supersede":
            r = self.supersede(_require("id"), _require("text"), scope=scope)
            return {"archived": r.archived, "new": r.new.id}
        if name == "forget":
            note = self.forget(_require("id"))
            return {"archived": note.id, "status": note.status}
        raise ToolError(f"unknown self-edit tool {name!r}")

    def optimize_prompt(self, name: str, trajectory: list[Msg], feedback: str) -> ProceduralResult:
        return self._procedural.optimize_prompt(name, trajectory, feedback)

    def get_procedural(self, name: str) -> str:
        return self._procedural.get_procedural(name)

    def set_procedural(self, name: str, text: str) -> Note:
        """Register/replace a behavior directive (procedural memory, SPEC §7)."""
        return self._procedural.set_procedural(name, text)
