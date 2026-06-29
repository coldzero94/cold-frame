# PROMPTS — concrete build-ready LLM prompt specs for every Coldframe LLM step (extraction, admission, conflict, merge/consolidation, procedural, summarize)

> where_it_goes: New focused doc: docs/prompts-spec.md (the contents of cold_frame/prompts/). SPEC §4 (Write Path) and §6/§7/§8 should add a one-line pointer to it. Also resolves audit findings: extraction confidence/durability gate, ADMISSION strictly-local tie-break (SPEC §4 invariant), CONFLICT dual-candidate continuous indices, and the held_for_human/quarantine routing for low-confidence extractions.


# Coldframe — Prompt Specs (build-ready) — `cold_frame/prompts/`

This doc is the complete spec for every LLM call in Coldframe. Each step gives: (a) the system+user prompt text/skeleton, (b) the exact output JSON schema, (c) few-shot examples, (d) the deterministic post-processing that maps LLM JSON → Coldframe `Note` fields (SPEC §2), (e) which gates apply. **Hard rule (D18): the LLM only proposes; deterministic code disposes.** All prompts target a provider-agnostic `LLM.complete_json(system, user, schema) -> dict` ABC; mock-injectable for eval (SPEC §10).

Shared conventions:
- All prompts demand: `Return ONLY valid JSON parsable by json.loads(). No prose, no markdown fences.`
- All ID references inside a single call use **contiguous integer indices starting at 0** (Graphiti style — UUIDs never sent to the LLM; we remap int↔uuid in code). This is the **UUID→int remap / anti-hallucination** rule from SPEC §4.
- Two date anchors are always passed: `observation_date` (when the conversation happened — the ONLY anchor for relative-time resolution) and `current_date` (today; never used to resolve relative refs). Both ISO-8601 UTC.
- Temperature 0 (or provider min) for all steps.
- Model size: extraction/conflict/merge can use the configured provider; **ADMISSION tie-break MUST be local** (see §2, hard invariant).

---

## 0. Prompt registry & file layout

```
cold_frame/prompts/
  __init__.py        # PROMPTS registry: name -> (system_fn, user_fn, schema_model, version_str)
  extract.py         # §1
  admission.py       # §2
  conflict.py        # §3  (dedup ambiguous-batch + conflict dual-candidate)
  consolidate.py     # §4  (episodic->semantic merge)
  procedural.py      # §5  (diagnose + edit + var-healer)
  summarize.py       # §6  (MCP summarize)
  cli.py mcp.py      # (servers — not prompts, kept per SPEC §12)
```

Each prompt module exports:
```python
PROMPT_VERSION = "extract/v1"          # bumped on any text change; logged into sources[].extractor
def system() -> str: ...
def user(ctx: dict) -> str: ...
class OutputSchema(BaseModel): ...     # pydantic, validates LLM JSON
```
`sources[].extractor` (SPEC §2 Source / R6 provenance invariant) is set to `f"{PROMPT_VERSION}@{model_id}"` so every LLM-written fact is traceable and re-runnable.

---

## 1. EXTRACTION  (`extract.py`)  — pipeline write path, single call

Synthesizes: mem0 ADDITIVE (self-contained, contextual richness, multi-topic, no-meta) + Graphiti anti-generalization (preserve proper nouns/numbers) + MemOS type classification (semantic/episodic/procedural) + observed-date grounding + per-fact confidence + durability hint. One LLM call per `add()`; outputs N atomic facts already shaped as Note candidates.

### 1.1 System prompt

```
You are Coldframe's Memory Extractor — a precise, evidence-bound processor. Your only
operation is to extract self-contained, atomic, contextually-grounded factual statements
about the user (and named speakers) from a conversation. You do not delete, merge, or judge
freshness — downstream deterministic code does that. You extract.

Each extracted fact must be ONE atomic, self-contained statement (one subject-predicate
idea), 15–80 words, with every pronoun and relative-time reference resolved. Split compound
sentences into separate facts. Never generalize concrete details. Classify each fact's type
and assign a confidence and a durability class.

Return ONLY valid JSON parsable by json.loads(). No prose. No markdown fences.
```

### 1.2 User prompt skeleton

```
## New Messages
{new_messages_json}            # [{"role":"user"|"assistant","content":"..."}]

## Last k Messages (context only — do NOT extract from here)
{last_k_messages}

## Recently Extracted (this session — dedup reference, do NOT re-extract)
{recently_extracted}           # ["text", ...]

## Existing Memories (relevant; dedup + linking only — do NOT extract from here)
{existing_memories}            # [{"id": 0, "text": "..."}, ...]   ints, remapped in code

## Observation Date (the ONLY anchor for relative time)
{observation_date}

## Current Date (today; NEVER use to resolve relative references)
{current_date}

# RULES
1. ATOMIC + SELF-CONTAINED. One idea per fact. Replace all pronouns with explicit names or "User".
   Resolve "yesterday"/"last week"/"recently" against Observation Date and write the absolute date.
   "User went to Paris last week" → "User visited Paris the week of {observation_date−7d}".
2. NEVER GENERALIZE. Preserve proper nouns, titles (in quotes), brand/model names, quantities,
   colors, qualifiers exactly. "promoted to assistant manager" stays "assistant manager", not
   "manager". "Ferrari 488 GTB" not "sports car". "416 pages" not "about 400".
3. MEANING-PRESERVING. "used to love X" = no longer; "didn't get to bed until 2AM" = late bedtime.
   Misreading is worse than skipping.
4. CAPTURE TRANSITIONS. When the user switches/replaces/stops something, the fact MUST state the
   new state AND what it replaced. "User switched from almond to oat milk after an almond sensitivity".
5. EXTRACT FROM BOTH ROLES. User messages = personal facts/preferences/plans/experiences.
   Assistant messages = ONLY genuinely new info (specific recommendations, plans created,
   researched facts). Skip echoes of what the user already said. In multi-speaker logs, a named
   speaker in the assistant role sharing their own life IS extractable, attributed by name.
6. EXTRACT CONTENT, NOT META. If the user shares a document/data/stat block, extract the facts
   FROM it, never "User shared a case summary".
7. MULTI-TOPIC. Scan every message; extract every distinct topic. Do not stop after the first.
8. NO FABRICATION / NO INFERRED ATTRIBUTES (gender/age/ethnicity from names). Every detail must
   trace to the input.
9. NOTHING WORTH EXTRACTING → return {"facts": []}.

# PER-FACT FIELDS (you must emit all of):
- text:        the atomic self-contained statement (15–80 words).
- memory_type: "semantic" = durable trait/preference/identity/relationship that persists
               ("User prefers dark roast coffee").
               "episodic" = a dated event/experience ("User visited Paris the week of 2026-05-08").
               "procedural" = an instruction on how an agent should behave / a workflow rule
               ("Always address the user in Korean"). When unsure between semantic and episodic:
               if it has a specific occurrence time → episodic; if it is a standing fact → semantic.
- keywords:    3–8 lowercase salient terms (entities/topics) for BM25.
- context:     ≤10-word one-line topic label.
- valid_at:    ISO-8601 UTC when the fact became true, resolved against Observation Date.
               For ongoing/standing facts use Observation Date. null only if truly unresolvable.
- confidence:  0.0–1.0 = how certain THIS extraction faithfully reflects an explicit statement.
               1.0 = user stated it directly & unambiguously. 0.6–0.8 = implied/assistant-sourced/
               paraphrased with mild inference. <0.4 = speculative/weakly-grounded (will be held
               for human review, NOT auto-persisted — see gate below). NEVER fabricate to raise it.
- importance:  0.0–1.0 = estimated long-term value to the user (identity/preference/decision = high;
               transient logistics = low). This is SEPARATE from confidence.
- durability:  "durable"  = identity/preference/decision/relationship/standing-procedure (persist).
               "ephemeral" = transient chatter, one-off logistics, momentary state (drop unless
                             confidence is high AND importance≥0.5).
- attributed_to: "user" or the speaker name.
- linked_ids:  array of ints from Existing Memories this fact relates to (same entity/topic,
               updated preference, continuation, or contradiction). [] if none.

# OUTPUT
{"facts": [ {<fields above>}, ... ]}
```

### 1.3 Output schema (pydantic)

```python
class ExtractedFact(BaseModel):
    text: str
    memory_type: Literal["semantic","episodic","procedural"]
    keywords: list[str] = []
    context: str = ""
    valid_at: str | None = None              # ISO-8601 UTC
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0, default=0.5)
    durability: Literal["durable","ephemeral"]
    attributed_to: str = "user"
    linked_ids: list[int] = []

class ExtractionOutput(BaseModel):
    facts: list[ExtractedFact]
```

### 1.4 Deterministic post-processing → Note (mem0/Graphiti remap + durability gate, SPEC §4)

For each `ExtractedFact f`:
1. **Durability gate (D18).** If `f.durability=="ephemeral"` and not (`f.confidence≥0.6` and `f.importance≥0.5`): **drop** (record nothing; eval counts as correctly-forgotten).
2. **Confidence gate → held_for_human.** If `f.confidence < 0.4`: create the Note but set `held_for_human=True`, `triage_reason="low_confidence_extraction"`, `status` stays `active` but it is **excluded from default search** (read FILTER adds `held_for_human=0`) until a human accepts in Triage (SPEC §6(c)). This is the build-ready resolution of the audit "quarantine" finding — implemented as a boolean flag + read-filter, NOT a new Status value.
3. **Provenance invariant (R6/D-T4).** Attach `Source(kind, ref, role=attributed_to, content_hash=sha256(raw_msg), observed_at=observation_date)` and `extractor=f"{PROMPT_VERSION}@{model_id}"`. A fact with zero sources cannot be persisted with `confidence>0.7` (clamp).
4. **Field map:** `content=f.text`, `memory_type`, `keywords`, `context`, `confidence`, `importance`, `valid_at=parse(f.valid_at) or observation_date`, `created_at=now`, `status="active"`, `version=1`, `decay_S=1.0`, `access_count=0`.
5. **linked_ids → edges:** for each remapped uuid, queue a `relates_to` edge (the conflict step §3 upgrades to `supersedes` if contradiction).
6. Pass each Note candidate to ADMISSION (§2) **before** DEDUP/CONFLICT/PERSIST.

### 1.5 Offline / `llm=None` fallback (SPEC §4 "naive 추출")
No LLM: one fact per user message, `text=message verbatim`, `memory_type="episodic"`, `confidence=0.5`, `importance=0.5`, `durability="durable"`, `valid_at=observation_date`. Still routed through ADMISSION→DEDUP→PERSIST.

### 1.6 Few-shot (embed 4–6; reuse mem0 examples that match Coldframe fields)
- Multi-topic: Marcus promotion + wife Elena + baby → 3 facts (semantic/semantic/episodic), confidence 0.9.
- Anti-generalization: "drove a Ferrari 488 GTB" → keep model, episodic, importance 0.4.
- Transition: almond→oat milk → semantic, durable, one fact stating the switch + reason.
- Document content: Bajimaya case → 3 episodic facts of the case content (NOT "user shared a case").
- Nothing: "Hi, good morning" → `{"facts": []}`.
- Low confidence: assistant speculation "you seem stressed" (user didn't confirm) → either skip or confidence 0.3 → held_for_human.

---

## 2. ADMISSION CLASSIFY  (`admission.py`) — strictly-local tie-break only

ADMISSION is **regex+entropy+NER first** (gitleaks-style secret regex + Shannon entropy; Presidio-style PII NER+regex+context; user TOML allow/deny). The LLM is invoked **only** for an ambiguous span the deterministic layer could not classify, and **only as a tie-breaker**.

### 2.1 HARD INVARIANT (resolves audit finding "admission LLM not pinned local")
> The ADMISSION tie-break LLM **MUST** be a strictly-local model (Ollama/llama.cpp), **regardless of the configured extraction/embedding provider** (D4 key-0, R11). Code enforces: if no local LLM is configured, the tie-break is **skipped and the span is treated as its higher-risk class** (fail-safe: ambiguous-secret → BLOCK, ambiguous-PII → REDACT). **No candidate-secret span is ever sent to a non-local endpoint.** This is a testable invariant: `test_admission_never_calls_remote()` asserts the remote client is never hit during admission, even when the extraction provider is OpenAI.

### 2.2 System prompt
```
You are a strictly-local, offline privacy classifier. You receive a single short text SPAN that
a regex/NER layer flagged as ambiguous. Decide ONLY its sensitivity class. You never see or store
anything; you emit one label. Return ONLY JSON.
```

### 2.3 User prompt
```
Classify the SPAN into exactly one class:
- "secret"  : a live credential / API key / password / private key / token / seed phrase — anything
              that grants access if leaked. (BLOCK: never persisted.)
- "pii"     : personal identifying data (email, phone, SSN/national-id, home address, full DOB,
              payment card). (REDACT: replaced by a typed placeholder; original not persisted.)
- "benign"  : neither. (ALLOW.)

Judge by whether disclosure causes access-loss (secret) or identity-exposure (pii). A random-looking
high-entropy string in a code-debugging context that is clearly an example/placeholder is benign.

<SURROUNDING_CONTEXT>
{context_window}            # ±1 sentence, for disambiguation only
</SURROUNDING_CONTEXT>

<SPAN>
{span}
</SPAN>

# OUTPUT
{"label": "secret"|"pii"|"benign", "pii_type": "<email|phone|ssn|address|card|other|null>", "reason": "<≤12 words>"}
```

### 2.4 Schema + disposition
```python
class AdmissionVerdict(BaseModel):
    label: Literal["secret","pii","benign"]
    pii_type: str | None = None
    reason: str
```
Code maps: `secret`→**BLOCK** (worthless tombstone only, never touches notes/fts/vec/history; see purge-invariant doc), `pii`→**REDACT** (replace span with `[REDACTED:{pii_type}]`, persist redacted text only), `benign`→**ALLOW** through to DEDUP. The LLM verdict is advisory on the *ambiguous span only*; deterministic regex verdicts always win.

---

## 3. CONFLICT / DEDUP  (`conflict.py`) — dual-candidate, continuous indices (Graphiti)

Two LLM uses, both **proposal-only**; freshness/archive are deterministic code (SPEC §4, §6).

### 3.A Ambiguous-dedup batch (only the cosine 0.82–0.93 band — SPEC §4/§6 Triage)
Exact/MinHash/clear-cosine dedup is code. Only near-dup pairs in the ambiguous band go to the LLM, batched.

System:
```
You are a fact deduplication assistant. NEVER mark facts with key differences (numbers, dates,
qualifiers, proper nouns) as duplicates. Return ONLY JSON.
```
User:
```
For each PAIR decide if NEW and EXISTING state the SAME fact at the SAME specificity.
Same meaning, different wording, no new specifics → "duplicate".
Any added/changed number, date, name, or qualifier → "distinct".

<PAIRS>
{pairs}     # [{"idx":0,"new":"...","existing":"..."}, ...]   contiguous idx
</PAIRS>

# OUTPUT
{"verdicts": [{"idx": 0, "relation": "duplicate"|"distinct"}, ...]}   # one per idx, same order
```
Disposition: `duplicate` → keep richer text, merge sources (non-destructive, `update_type="dedup"` in note_history), do not create a new note. `distinct` → continue to conflict.

### 3.B Conflict dual-candidate (duplicate vs contradiction, ONE call — Graphiti dedupe_edges)

This is the core SPEC §4 CONFLICT prompt. Candidates are retrieved by same topic/endpoint, then passed as TWO lists with **continuous idx across both** (EXISTING first, then INVALIDATION CANDIDATES).

System:
```
You are a fact conflict-resolution assistant for a personal memory store. You decide ONLY whether
a NEW FACT duplicates or contradicts known facts. You do NOT decide which is newer or what to
archive — deterministic code does that using timestamps. Never mark facts with key differences
(numbers, dates, qualifiers) as duplicates. Return ONLY JSON.
```
User:
```
You receive TWO lists with CONTINUOUS idx numbering (EXISTING FACTS first, then CONTRADICTION
CANDIDATES start where EXISTING ends).

<EXISTING FACTS>
{existing_facts}        # [{"idx":0,"text":"...","valid_at":"..."}, ...]
</EXISTING FACTS>

<CONTRADICTION CANDIDATES>
{candidates}            # [{"idx":3,"text":"...","valid_at":"..."}, ...]  continues idx
</CONTRADICTION CANDIDATES>

<NEW FACT>
{new_fact}              # {"text":"...","valid_at":"..."}
</NEW FACT>

1. DUPLICATE: idx values (ONLY from EXISTING FACTS) whose factual content is identical to NEW FACT.
2. CONTRADICTION: idx values (from EITHER list) the NEW FACT contradicts — same subject+relation
   but an incompatible value (e.g. "works at X" vs "works at Y"; "lives in A" vs "moved to B").
   A fact can be BOTH duplicate and contradicted (same relation, updated value).
   Different events on different days are NEITHER.

# OUTPUT
{"duplicate_idx": [..], "contradicted_idx": [..]}
```
Schema:
```python
class ConflictVerdict(BaseModel):
    duplicate_idx: list[int] = []
    contradicted_idx: list[int] = []
```

### 3.C Deterministic disposition (LLM does NOT do this — SPEC §4 철칙)
For each `contradicted_idx i` → remap to uuid, compare `valid_at`:
- `new.valid_at > old.valid_at` → old `status="archived"`, `old.invalid_at = new.valid_at`, create `supersedes` edge (new→old), `note_history(update_type="conflict")`. Persist new active.
- `new.valid_at < old.valid_at` → the NEW fact is stale → new gets `invalid_at = old.valid_at` (Graphiti rule); old stays active.
- `valid_at` equal **or** either null (no time signal) → cannot decide → set `held_for_human=True, triage_reason="contradiction_tie"` on the NEW note (SPEC §6 Triage (a)). Do not archive anything.
`duplicate_idx` → handled as §3.A duplicate (merge, no new note).

---

## 4. MERGE / CONSOLIDATION  (`consolidate.py`) — episodic→semantic, non-destructive

Background `consolidate()` clusters same-topic **episodic** notes and asks the LLM to synthesize ONE semantic summary note. **Never collapses procedural notes** (they have their own optimize path §5). Original episodics are NOT deleted — they are cold-demoted; the summary links via `derived_from` edges.

System (adapts Graphiti summarize_pair):
```
You combine several related episodic memories into ONE dense, semantic summary fact. Preserve every
materially relevant name, role, place, date, count, and change-over-time that is explicitly supported
by the inputs. Prefer compact factual sentences. Do not invent. Do not include procedural
instructions. Return ONLY JSON.
```
User:
```
These EPISODIC memories are about the same topic. Produce ONE semantic summary capturing the durable
takeaway across them (the standing fact, preference, or pattern they collectively establish). Keep
all explicitly-supported proper nouns, numbers, and dated changes. ≤ 80 words.

<EPISODIC MEMORIES>
{episodic_cluster}    # [{"idx":0,"text":"...","valid_at":"..."}, ...]
</EPISODIC MEMORIES>

# OUTPUT
{"summary": "<semantic fact>", "keywords": ["..."], "valid_at": "<ISO-8601 of earliest supported>",
 "source_idx": [0,1,2]}     # which inputs the summary is derived from
```
Schema:
```python
class ConsolidationOutput(BaseModel):
    summary: str
    keywords: list[str] = []
    valid_at: str | None = None
    source_idx: list[int]
```
Disposition (deterministic, non-destructive — SPEC §6.2):
- Create new Note: `content=summary`, `memory_type="semantic"`, `keywords`, `valid_at`, `importance=max(sources.importance)`, `confidence=min(sources.confidence)`, `derived_from` edges to each `source_idx` uuid.
- Source episodics: `status` stays `active` but `decay_S` reset lower / importance unchanged → they naturally cold-demote; **do not archive on creation** (revivable). Capacity-cap (§6.4) may later archive lowest-score originals.
- `note_history(update_type="consolidation")` on the new note.

---

## 5. PROCEDURAL GRADIENT  (`procedural.py`) — diagnose→edit two-stage + var-healer

Implements `optimize_prompt(name, trajectory, feedback)` (SPEC §7, langmem gradient). Two LLM calls; a drift-prevention gate between them; a deterministic var-healer wrapping the edit. Stored as a normal `procedural` Note with version history (rollback).

### 5.1 Stage 1 — DIAGNOSE (gradient gate)
System:
```
You are reviewing an AI assistant's behavior under a given instruction (prompt fragment). Recommend
changes ONLY if there is concrete evidence of failure in the trajectory. Be minimally invasive.
Return ONLY JSON.
```
User:
```
<current_instruction>
{prompt}
</current_instruction>

<update_instructions>          # developer guidance on when/how to change
{update_instructions}
</update_instructions>

<trajectory>                   # the session(s) + any user feedback
{trajectories}
{feedback}
</trajectory>

Analyze: did the assistant fulfill intent? Where did it deviate? Identify failure mode(s)
(style mismatch, unclear instruction, flawed logic, hallucination). If the instruction performed
well, set warrants_adjustment=false and stop. Only recommend changes tied to observed failures.

# OUTPUT
{"warrants_adjustment": true|false, "hypotheses": "<why it failed, or ''>",
 "recommendations": "<concrete minimal edits, or ''>"}
```
Schema:
```python
class DiagnoseOutput(BaseModel):
    warrants_adjustment: bool
    hypotheses: str = ""
    recommendations: str = ""
```
**Gate (drift prevention, SPEC §7.1):** if `warrants_adjustment is False` → return the original instruction unchanged. No edit call, no new version.

### 5.2 Stage 2 — EDIT (constrained rewrite)
Only runs if Stage 1 warranted. System:
```
You are rewriting an instruction to fix the diagnosed failures. Make ONLY the changes required by
the recommendations — minimally invasive. You MUST retain every f-string variable exactly as it
appears (e.g. {user_name}); do not add, rename, or remove variables. Return ONLY JSON.
```
User:
```
<current_instruction>
{current_prompt}
</current_instruction>

<hypotheses>
{hypotheses}
</hypotheses>

<recommendations>
{recommendations}
</recommendations>

The instruction contains these required variables you MUST keep verbatim: {required_vars}.

# OUTPUT
{"analysis": "<plan>", "improved_prompt": "<full rewritten instruction>"}
```
Schema:
```python
class EditOutput(BaseModel):
    analysis: str
    improved_prompt: str
```

### 5.3 Var-healer (deterministic, langmem `get_var_healer`) — runs on `improved_prompt`
1. Extract `required_vars = set(re.findall(r"\{(.+?)\}", current_prompt))`.
2. **assert_all_required:** every `{var}` in `required_vars` must appear in `improved_prompt` → else **hard-fail** (raise; keep old version, do NOT persist).
3. **mask** each `{var}` → uuid hex; **escape** any other stray `{`/`}` → `{{`/`}}`; strip `<TO_OPTIMIZE>` markers; **unmask** uuids → `{var}`.
4. Persist healed text as a new version of the procedural Note (`note_history(update_type="manual"|"feedback")`), incrementing `version`. Rollback = revert to prior `note_history` snapshot.

---

## 6. SUMMARIZE  (`summarize.py`) — MCP `summarize(topic?)` tool

Read-path tool (SPEC §8): gather relevant active facts via `search`, summarize. Read-only; writes nothing.

System:
```
You summarize a set of remembered facts into a concise, faithful answer. Use ONLY the provided
facts. Do not invent. Cite which fact ids you used. Return ONLY JSON.
```
User:
```
<topic>
{topic}            # may be empty → general "what is known"
</topic>

<facts>
{facts}            # [{"id":0,"text":"...","valid_at":"...","strength":0.x}, ...]  contiguous id
</facts>

Write a compact factual summary answering the topic from these facts. Prefer recent/high-strength
facts when they conflict. ≤ 120 words. List the fact ids you actually used.

# OUTPUT
{"summary": "<text>", "used_ids": [0,2,5]}
```
Schema:
```python
class SummarizeOutput(BaseModel):
    summary: str
    used_ids: list[int]
```
Disposition: remap `used_ids` → uuids → return MCP `{summary, fact_ids:[uuid,...]}` (SPEC §8). No persistence; counts as a read (REINFORCE applies to `used_ids`).

---

## 7. Cross-cutting build notes

- **Determinism for eval (SPEC §10):** every step calls `LLM.complete_json`; the eval harness injects a mock returning canned JSON keyed by call-site, so dedup/conflict/decay are tested without a live model.
- **Anti-hallucination remap (SPEC §4):** the LLM only ever sees contiguous int indices; the int→uuid map lives in the calling code. If the LLM returns an idx out of range, that idx is dropped (logged), never crashes the write.
- **Provenance/extractor stamping (R6):** `PROMPT_VERSION` strings (`extract/v1`, `conflict/v1`, …) are written into `sources.extractor`/note_history so a prompt change is auditable and facts are re-derivable.
- **Versioning prompts:** changing any prompt text bumps `PROMPT_VERSION`; eval golden sets are keyed per version.
