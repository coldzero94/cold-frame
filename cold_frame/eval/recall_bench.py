"""Recall-quality benchmark — the adoption proof (does semantic recall actually help?).

The golden suites are pass/fail on vocab-matched queries; this measures ``recall@k`` on a realistic
corpus split into two query kinds:

- **lexical** — the query shares content words with its fact (BM25/HashEmbedder find it).
- **paraphrase** — the query means the same thing with (near-)ZERO token overlap. A bag-of-words
  embedder (the offline ``HashEmbedder`` default) CANNOT bridge the vocabulary gap; a real semantic
  embedder (``[local-llm]`` ``bge-small``) can.

Honest, reproducible finding: HashEmbedder ≈ full recall on lexical queries but drops sharply on
paraphrases, and the local embedder lifts it. Run ``python -m cold_frame.eval.recall_bench``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from cold_frame.api import Memory
from cold_frame.llm.base import Embedder

# (fact, lexical query, paraphrase query). Paraphrase queries deliberately avoid the fact's content
# words so lexical recall can't cheat — the match is purely semantic.
CORPUS: list[tuple[str, str, str]] = [
    (
        "I prefer dark roast coffee in the morning",
        "dark roast coffee",
        "what caffeinated beverage do I drink at breakfast",
    ),
    (
        "my favorite programming language is Python",
        "favorite programming language",
        "which coding tool do I write software in",
    ),
    (
        "I live in Seoul near the Han river",
        "where I live Seoul",
        "which city is my home in South Korea",
    ),
    (
        "I drive a red Ferrari 488 on weekends",
        "what Ferrari do I drive",
        "what sports car do I own",
    ),
    ("my dog is a golden retriever named Max", "golden retriever Max", "what pet animal do I keep"),
    (
        "I work as a backend engineer at a fintech startup",
        "backend engineer fintech",
        "what is my occupation",
    ),
    (
        "I am allergic to peanuts and shellfish",
        "peanut shellfish allergy",
        "which foods must I avoid eating",
    ),
    (
        "I usually go to bed around midnight",
        "when I go to bed",
        "what time do I fall asleep at night",
    ),
    ("my mother's birthday is in March", "mother birthday March", "when was my mom born"),
    ("I use Vim as my text editor", "Vim text editor", "what do I edit source files with"),
    (
        "I am learning to play the piano",
        "learning piano",
        "which musical instrument am I practicing",
    ),
    ("I take my long vacation in the summer", "when is my vacation", "which season do I travel in"),
]


def _recall_at_k(m: Memory, ids: list[str], queries: list[str], k: int) -> float:
    hit = sum(
        1 for i, q in enumerate(queries) if ids[i] in {h.note.id for h in m.search(q, k=k).hits}
    )
    return hit / len(queries)


def evaluate(embedder: Embedder, *, k: int = 3) -> dict[str, float]:
    """Seed the corpus with ``embedder`` and return lexical vs paraphrase ``recall@k``."""
    db = str(Path(tempfile.mkdtemp()) / "recall_bench.db")
    m = Memory(db, embedder=embedder)
    ids = [m.add(fact, raw=True).added[0].id for fact, _, _ in CORPUS]
    return {
        "lexical": _recall_at_k(m, ids, [lex for _, lex, _ in CORPUS], k),
        "paraphrase": _recall_at_k(m, ids, [para for _, _, para in CORPUS], k),
        "k": float(k),
        "n": float(len(CORPUS)),
    }


def format_report(hash_r: dict[str, float], local_r: dict[str, float] | None) -> str:
    """A human-readable table (hash always; local when the [local-llm] extra is present)."""
    k, n = int(hash_r["k"]), int(hash_r["n"])
    lines = [
        f"Recall@{k} over {n} facts (lexical vs paraphrase queries)",
        f"  {'embedder':<24} {'lexical':>9} {'paraphrase':>12}",
        f"  {'hash (offline default)':<24} {hash_r['lexical']:>9.0%} {hash_r['paraphrase']:>12.0%}",
    ]
    if local_r is not None:
        lines.append(
            f"  {'local (bge-small)':<24} {local_r['lexical']:>9.0%} {local_r['paraphrase']:>12.0%}"
        )
        lift = local_r["paraphrase"] - hash_r["paraphrase"]
        lines.append(f"\n  → semantic recall lifts paraphrase recall by {lift:+.0%}.")
    else:
        lines.append(
            "\n  (install cold-frame[local-llm] + set COLD_FRAME_EMBEDDER=local to compare)"
        )
    lines.append(
        "\n  Lexical queries share words with the fact (bag-of-words finds them); paraphrase"
        "\n  queries do NOT — only a semantic embedder bridges the vocabulary gap."
    )
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - human-facing report
    import importlib.util

    from cold_frame.llm import HashEmbedder, resolve_embedder

    hash_r = evaluate(HashEmbedder())
    local_r = (
        evaluate(resolve_embedder("local"))
        if importlib.util.find_spec("sentence_transformers")
        else None
    )
    print(format_report(hash_r, local_r))


if __name__ == "__main__":  # pragma: no cover
    main()
