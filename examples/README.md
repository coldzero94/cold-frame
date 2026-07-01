# Coldframe examples & recipes

## Quickstart

```bash
python examples/quickstart.py
```

The core loop, fully offline (no key, no network): add facts → recall → correct a belief
(deterministic supersession) → the old belief is retained and revivable.

## Recipes

### Turn on semantic recall (opt-in)

The zero-config default recall is **lexical** (offline `HashEmbedder` — no deps, no download). It
finds facts that share words with your query, but misses paraphrases. For semantic recall:

```bash
pip install 'cold-frame[local-llm]'          # a small local bge-small model
export COLD_FRAME_EMBEDDER=local              # CLI + MCP now embed semantically
cold-frame reembed                            # re-index existing notes under the new embedder
```

Nothing leaves the machine (the model runs in-process). Measure the difference yourself:

```bash
python -m cold_frame.eval.recall_bench        # recall@k: hash vs local, lexical vs paraphrase
```

The default `HashEmbedder` scores ~100% recall on lexical queries but ~33% on paraphrases; the local
embedder bridges that vocabulary gap.

### Turn on automatic conflict detection (opt-in)

Supersession is **always** deterministic (code decides by `valid_at`, never the LLM). But *detecting*
that two facts contradict needs a model. The offline default does duplicate-merging + honors explicit
`correct` / `supersede`; to auto-detect contradictions:

```bash
export COLD_FRAME_LLM=claude    # uses your Claude Code session (no API key) for the dedup/conflict judges
```

The `cold-frame worker` already uses this when the `claude` CLI is on PATH. Obvious secrets in the
text are never sent to the model (the pre-send scan falls back to local extraction).

### Back up / move / encrypt

```bash
cold-frame export backup.db                   # a consistent snapshot (or --events for an NDJSON log)
cold-frame import backup.db                    # restore (or import <log.ndjson> --events to replay)

pip install 'cold-frame[crypto]'
cold-frame encrypt --out enc.db --key "…"      # write an encrypted copy (or $COLD_FRAME_KEY)
cold-frame rekey --new-key "…"                 # rotate the at-rest key
```

### Browse what it knows

```bash
cold-frame ui         # local web UI at 127.0.0.1:27182 (falls back to an inline inspector)
cold-frame doctor     # health: counts, integrity, embedder id, stale-vector count
```
