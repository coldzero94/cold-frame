"""Lightweight deterministic secret admission (I6 — v1 scope, D25).

No LLM, no key, no network: a gitleaks-style regex + Shannon-entropy scan that BLOCKs obvious
secrets/credentials BEFORE they touch disk. Returns only a ``(reason, placeholder)`` label —
NEVER the matched content (I6/I16). Plus ``redact_pii`` — an OPT-IN deterministic PII scrub that
replaces emails/phones/cards/SSNs inline before persist (off by default: a personal-memory tool must
not blanket-redact the user's OWN useful contact facts; enable per-category when wanted). The
LLM CONFIDENCE-GATE/CONSENT tiebreak (I7) and crypto-shred purge remain deferred (v1.1 / hosted).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Literal

# (kind label, pattern). The kind goes in the placeholder; the matched text is never returned.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("api_key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
]
# `password = ...` / `api_key: ...` style assignments.
_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)\b\s*[:=]\s*"
    r"\S{6,}"
)
_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")  # a contiguous long blob (entropy backstop)
_ENTROPY_MIN = 4.5  # Shannon bits/char; random base64 secrets ~4.5-6, hex UUIDs only ~4.0

Verdict = tuple[Literal["secret", "credential"], str]  # (reason, placeholder) — never content


def _entropy(s: str) -> float:
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def scan_secret(text: str) -> Verdict | None:
    """``(reason, placeholder)`` if ``text`` holds an obvious secret/credential, else ``None``."""
    for kind, pat in _PATTERNS:
        if pat.search(text):
            return ("secret", f"[BLOCKED:{kind}]")
    if _ASSIGNMENT.search(text):
        return ("credential", "[BLOCKED:credential]")
    for token in _TOKEN.findall(text):
        if _entropy(token) >= _ENTROPY_MIN:
            return ("secret", "[BLOCKED:high_entropy]")
    return None


# ── PII redaction (OPT-IN, I6 REDACT — deterministic, no LLM) ──────────────────
PiiCategory = Literal["email", "phone", "credit_card", "ssn"]
PII_CATEGORIES: frozenset[str] = frozenset({"email", "phone", "credit_card", "ssn"})

# (category, pattern, placeholder, optional digit-count validator). Order matters: the more
# specific structured patterns (email, ssn, card) run BEFORE the loose phone pattern so it can't
# swallow them. Validators reject digit blobs that look like ports/versions, not real PII.
_PII: list[tuple[PiiCategory, re.Pattern[str], str]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[email]"),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    ("credit_card", re.compile(r"\b\d(?:[ -]?\d){12,15}\b"), "[card]"),
    ("phone", re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)"), "[phone]"),
]


def _digit_count(s: str) -> int:
    return sum(c.isdigit() for c in s)


def redact_pii(
    text: str, categories: frozenset[str] = PII_CATEGORIES
) -> tuple[str, dict[str, int]]:
    """Replace PII spans with typed placeholders; return ``(redacted_text, {category: count})``.

    Deterministic + content-free in the summary (counts only, never the matched value — I16). The
    redacted text keeps the surrounding fact intact ("my email is [email]"). card/phone are
    validated by digit count (13-16 / 10-15) so ports, versions, and counts are NOT redacted.
    """
    summary: Counter[str] = Counter()
    for cat, pat, placeholder in _PII:
        if cat not in categories:
            continue

        def _sub(m: re.Match[str], _cat: str = cat, _ph: str = placeholder) -> str:
            span = m.group(0)
            if _cat == "credit_card" and not (13 <= _digit_count(span) <= 16):
                return span
            if _cat == "phone" and not (10 <= _digit_count(span) <= 15):
                return span
            summary[_cat] += 1
            return _ph

        text = pat.sub(_sub, text)
    return text, dict(summary)
