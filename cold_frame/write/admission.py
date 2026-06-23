"""Lightweight deterministic secret admission (I6 — v1 scope, D25).

No LLM, no key, no network: a gitleaks-style regex + Shannon-entropy scan that BLOCKs obvious
secrets/credentials BEFORE they touch disk. Returns only a ``(reason, placeholder)`` label —
NEVER the matched content (I6/I16). The heavier CLASSIFY→REDACT→CONSENT pipeline, the local-only
tiebreak (I7), and the crypto-shred purge remain deferred (v1.1 / hosted).
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
