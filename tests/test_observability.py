"""I16: structured logs never carry note content/secrets — the redact filter masks the denylist."""

from __future__ import annotations

import json
import logging

from cold_frame.observability import REDACTED, JsonFormatter, RedactFilter


def test_logs_have_no_content() -> None:
    fmt = JsonFormatter()
    rec = logging.LogRecord("cold_frame.test", logging.INFO, __file__, 1, "an_event", None, None)
    # denylisted fields carry sensitive values; safe fields are ids/tasks/counters
    sensitive = {
        "content": "SENTINEL_note_body",
        "text": "SENTINEL_raw_text",
        "payload": "SENTINEL_payload",
        "user": "SENTINEL_user",
        "raw": "SENTINEL_raw",
        "span": "SENTINEL_secret_span",
    }
    for k, v in {**sensitive, "note_id_hash": 12345, "task": "dedup_batch"}.items():
        setattr(rec, k, v)

    out = fmt.format(rec)
    assert "SENTINEL" not in out  # no sensitive value leaks, in any field
    payload = json.loads(out)
    for k in sensitive:
        assert payload[k] == REDACTED  # every denylisted field is masked
    assert payload["note_id_hash"] == 12345 and payload["task"] == "dedup_batch"  # safe fields kept


def test_redact_filter_masks_denylist_in_place() -> None:
    rec = logging.LogRecord("cold_frame.test", logging.INFO, __file__, 1, "e", None, None)
    rec.content = "SENTINEL_secret"  # type: ignore[attr-defined]
    rec.task = "conflict_judge"  # type: ignore[attr-defined]
    RedactFilter().filter(rec)
    assert rec.content == REDACTED  # type: ignore[attr-defined]
    assert rec.task == "conflict_judge"  # type: ignore[attr-defined] - safe field untouched
