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


def test_logs_mask_nested_content_recursively() -> None:
    # I16: a denylisted key NESTED under a non-denylisted key must still be masked (a top-level-only
    # mask would leak content via extra={"meta": {"content": "..."}} or a list of dicts).
    fmt = JsonFormatter()
    rec = logging.LogRecord("cold_frame.test", logging.INFO, __file__, 1, "ev", None, None)
    rec.meta = {"note_id": "ok", "content": "SENTINEL_nested", "deep": {"payload": "SENTINEL_deep"}}
    rec.items = [{"text": "SENTINEL_in_list"}]
    out = fmt.format(rec)
    assert "SENTINEL" not in out  # no nested sensitive value leaks at any depth
    payload = json.loads(out)
    assert payload["meta"]["note_id"] == "ok"  # safe nested key kept
    assert payload["meta"]["content"] == REDACTED
    assert payload["meta"]["deep"]["payload"] == REDACTED
    assert payload["items"][0]["text"] == REDACTED


def test_unsafe_trace_formatter_exposes_content() -> None:
    # the documented "only content path": redact=False must actually surface denylisted fields
    fmt = JsonFormatter(redact=False)
    rec = logging.LogRecord("cold_frame.test", logging.INFO, __file__, 1, "ev", None, None)
    rec.content = "VISIBLE_trace"
    assert "VISIBLE_trace" in fmt.format(rec)


def test_set_log_level_updates_all_cold_frame_loggers() -> None:
    # -v/-q wire through set_log_level; cold_frame loggers are propagate=False so each must be set.
    from cold_frame.observability import get_logger, set_log_level

    lg = get_logger("cold_frame.leveltest")
    try:
        set_log_level(logging.ERROR)
        assert lg.level == logging.ERROR  # an already-configured logger is updated
        assert get_logger("cold_frame.leveltest2").level == logging.ERROR  # ...and a future one
    finally:
        set_log_level(logging.INFO)  # restore the module default so other tests aren't affected


def test_cli_verbosity_flags_set_diagnostic_level(tmp_path: object) -> None:
    import logging as _logging

    from cold_frame.cli import main
    from cold_frame.observability import get_logger, set_log_level

    db = str(tmp_path / "m.db")  # type: ignore[operator]
    try:
        main(["--db", db, "-q", "stats"])
        assert get_logger("cold_frame.cli").level == _logging.ERROR
        main(["--db", db, "-v", "stats"])
        assert get_logger("cold_frame.cli").level == _logging.INFO
        main(["--db", db, "stats"])  # no flag → quiet default
        assert get_logger("cold_frame.cli").level == _logging.WARNING
    finally:
        set_log_level(_logging.INFO)


def test_cli_vv_flag_is_debug(tmp_path: object) -> None:
    import logging as _logging

    from cold_frame.cli import main
    from cold_frame.observability import get_logger, set_log_level

    try:
        main(["--db", str(tmp_path / "m.db"), "-vv", "stats"])  # type: ignore[operator]
        assert get_logger("cold_frame.cli").level == _logging.DEBUG
    finally:
        set_log_level(_logging.INFO)
