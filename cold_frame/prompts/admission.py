"""Admission tiebreak prompt (I7 / I6) — the LOCAL-only secret judge for an AMBIGUOUS span.

The deterministic scan (``write/admission.scan_secret``) BLOCKs obvious secrets and passes obvious
clean text. For the narrow ambiguous band in between, a LOCAL LLM (I7: never remote) decides. The
LLM only judges secret-or-not; the code disposes (fail-closed on can't-verify). The span — never the
full content — is the only thing sent, and only to a local model.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel


class AdmissionVerdict(BaseModel):
    """The local tiebreak's judgement for one ambiguous span."""

    is_secret: bool


ADMISSION_SYSTEM: Final[str] = (
    "You are a security admission check for a personal memory store. Decide ONLY whether the given "
    "SPAN is a live secret/credential (API key, access token, password, private key) that must NOT "
    "be stored. A hash, UUID, commit SHA, version string, file path, or ordinary identifier is NOT "
    "a secret. When genuinely unsure, answer is_secret=true (fail safe). Return ONLY valid JSON."
)


def build_admission_user(span: str) -> str:
    return f'SPAN: {span}\n\nReturn {{"is_secret": true}} or {{"is_secret": false}}.'
