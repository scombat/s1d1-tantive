"""The local spool over the capture files (instance id, v1 lines, cursor, ordering, skipped lines) and the sender."""

from __future__ import annotations

import http.server
import json
import threading
import time
import uuid
from pathlib import Path

import pytest

from tantive import __version__
from tantive.send import Cursor, Sender, Spool, instance_id, post_ndjson, to_ingest
from tantive.telemetry import configure_logging
from tests.test_capture import counter, make, offer


def row(ident: str, send: bool = True, **extra) -> dict:
    """A captured line; `send=True` (the default) marks it as written while sending was on."""
    out = {
        "time": "2026-10-24T10:00:00.000Z",
        "id": ident,
        "prompt": f"prompt {ident}",
        "format": "chat",
        "route": "/v1/chat/completions",
        "tier": "LOW",
        "score": 0.2,
        "task": "qa",
        "needs_tools": 0.1,
        "confidence": 0.9,
        "pieces": 3,
        "truncated": False,
        "prompt_tokens": 3,
        "latency_ms": 1.5,
        "variant": "small",
        "revision": "abc1234",
        "service_version": "0.8.0",
        "terms_version": "v1",
        **extra,
    }
    if send:
        out["send"] = True
    return out


def line(ident: str, send: bool = True, **extra) -> str:
    return json.dumps(row(ident, send, **extra), ensure_ascii=False) + "\n"


def write(directory: Path, name: str, ids: list[str], big: str | None = None) -> None:
    extra = {"prompt": big} if big else {}
    (directory / name).write_text("".join(line(i, **extra) for i in ids), encoding="utf-8")


def ids(rows: list[dict]) -> list[str]:
    return [r["id"] for r in rows]


def test_instance_id_is_stable(tmp_path):
    a = instance_id(tmp_path)
    assert a == instance_id(tmp_path) and uuid.UUID(a)
    assert (tmp_path / ".instance-id").read_text().strip() == a


def test_an_existing_instance_id_is_kept(tmp_path):
    (tmp_path / ".instance-id").write_text("fixed-id\n")
    assert instance_id(tmp_path) == "fixed-id"


def test_to_ingest_maps_the_fields():
    r = {
        "id": "i",
        "time": "t",
        "prompt": "p",
        "format": "chat",
        "tier": "LOW",
        "score": 0.2,
        "task": "qa",
        "needs_tools": 0.1,
        "confidence": 0.9,
        "variant": "small",
        "revision": "abc",
        "terms_version": "v1",
        "send": True,
    }
    out = to_ingest(r, "inst")
    assert list(out) == [
        "v",
        "id",
        "time",
        "prompt",
        "format",
        "instance_id",
        "variant",
        "model_revision",
        "terms_version",
        "pred_tier",
        "pred_score",
        "pred_task",
        "pred_needs_tools",
        "pred_confidence",
    ]
    assert (out["v"], out["instance_id"], out["model_revision"], out["pred_tier"]) == (1, "inst", "abc", "LOW")
    assert (out["pred_score"], out["pred_task"], out["pred_needs_tools"], out["pred_confidence"]) == (
        0.2,
        "qa",
        0.1,
        0.9,
    )


def test_to_ingest_defaults_missing_keys_but_not_the_terms_version():
    out = to_ingest({"id": "i", "terms_version": "v2"}, "inst")
    assert out["terms_version"] == "v2"
    assert out["pred_tier"] is None and out["model_revision"] is None and out["prompt"] is None
    with pytest.raises(KeyError):
        to_ingest({"id": "i"}, "inst")  # a marked line always has it: no default


def test_to_ingest_drops_the_local_only_fields():
    out = to_ingest(row("a"), "inst")
    assert not {"route", "pieces", "truncated", "prompt_tokens", "latency_ms", "service_version", "send"} & set(out)


def test_an_empty_directory_reads_nothing(tmp_path):
    rows, cursor, skipped = Spool(tmp_path).read(10)
    assert (rows, skipped) == ([], 0)
    Spool(tmp_path).commit(cursor)
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    assert ids(Spool(tmp_path).read(10)[0]) == ["a"]


def test_files_are_read_in_day_then_index_order(tmp_path):
    write(tmp_path, "capture-2026-10-24-001.jsonl", ["b"])
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    write(tmp_path, "capture-2026-10-23.jsonl", ["z"])
    rows, _, _ = Spool(tmp_path).read(10)
    assert ids(rows) == ["z", "a", "b"]  # lexical order would put -001 before .jsonl


def test_other_files_in_the_directory_are_ignored(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    (tmp_path / "notes.jsonl").write_text(line("x"))
    (tmp_path / ".instance-id").write_text("whatever")
    assert ids(Spool(tmp_path).read(10)[0]) == ["a"]


def test_a_partial_last_line_waits(tmp_path):
    (tmp_path / "capture-2026-10-24.jsonl").write_text(line("a") + '{"id": "b", "pro')
    rows, cursor, skipped = Spool(tmp_path).read(10)
    assert ids(rows) == ["a"] and cursor.offset == len(line("a").encode()) and skipped == 0


def test_a_partial_line_completed_later_is_read(tmp_path):
    path = tmp_path / "capture-2026-10-24.jsonl"
    path.write_text(line("a") + line("b")[:20])
    s = Spool(tmp_path)
    s.commit(s.read(10)[1])
    with path.open("a") as f:
        f.write(line("b")[20:])
    assert ids(Spool(tmp_path).read(10)[0]) == ["b"]


def test_limit_counts_rows_and_the_cursor_stops_after_them(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b", "c"])
    rows, cursor, _ = Spool(tmp_path).read(2)
    assert ids(rows) == ["a", "b"]
    assert cursor == Cursor("capture-2026-10-24.jsonl", len(line("a").encode()) * 2)


def test_commit_then_read_continues(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b", "c"])
    s = Spool(tmp_path)
    _, cursor, _ = s.read(2)
    s.commit(cursor)
    assert ids(Spool(tmp_path).read(10)[0]) == ["c"]


def test_without_commit_the_same_rows_come_back(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b"])
    assert ids(Spool(tmp_path).read(1)[0]) == ids(Spool(tmp_path).read(1)[0]) == ["a"]


def test_the_cursor_is_json_and_leaves_no_temporary_file(tmp_path):
    s = Spool(tmp_path)
    s.commit(Cursor("capture-2026-10-24.jsonl", 42))
    assert json.loads((tmp_path / ".send-cursor").read_text()) == {"file": "capture-2026-10-24.jsonl", "offset": 42}
    assert sorted(p.name for p in tmp_path.iterdir()) == [".send-cursor"]


def test_a_batch_spans_files_and_the_cursor_moves_to_the_last_one(tmp_path):
    write(tmp_path, "capture-2026-10-23.jsonl", ["a"])
    write(tmp_path, "capture-2026-10-24.jsonl", ["b", "c"])
    s = Spool(tmp_path)
    rows, cursor, _ = s.read(10)
    assert ids(rows) == ["a", "b", "c"]
    assert cursor == Cursor("capture-2026-10-24.jsonl", len(line("b").encode()) * 2)
    s.commit(cursor)
    assert s.read(10)[0] == []


def test_a_new_file_after_the_cursor_is_picked_up(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    s = Spool(tmp_path)
    s.commit(s.read(10)[1])
    write(tmp_path, "capture-2026-10-24-001.jsonl", ["b"])
    assert ids(Spool(tmp_path).read(10)[0]) == ["b"]


def test_a_purged_cursor_file_moves_on(tmp_path):
    write(tmp_path, "capture-2026-10-23.jsonl", ["a"])
    write(tmp_path, "capture-2026-10-24.jsonl", ["b"])
    s = Spool(tmp_path)
    s.commit(s.read(1)[1])
    (tmp_path / "capture-2026-10-23.jsonl").unlink()
    assert ids(Spool(tmp_path).read(10)[0]) == ["b"]


def test_files_older_than_a_purged_cursor_are_not_replayed(tmp_path):
    write(tmp_path, "capture-2026-10-23.jsonl", ["a"])
    s = Spool(tmp_path)
    s.commit(s.read(1)[1])
    (tmp_path / "capture-2026-10-23.jsonl").unlink()
    write(tmp_path, "capture-2026-10-24.jsonl", ["b"])
    (tmp_path / "capture-2026-10-22.jsonl").write_text(line("old"))  # older than the cursor: never read again
    assert ids(Spool(tmp_path).read(10)[0]) == ["b"]


def test_no_cursor_starts_at_the_oldest_file(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["b"])
    write(tmp_path, "capture-2026-10-22.jsonl", ["a"])
    assert ids(Spool(tmp_path).read(10)[0]) == ["a", "b"]


def test_an_unreadable_cursor_starts_over(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    (tmp_path / ".send-cursor").write_text("{not json")
    assert ids(Spool(tmp_path).read(10)[0]) == ["a"]


def test_an_oversized_line_is_skipped_and_counted(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"], big="x" * 2_000_000)
    rows, cursor, skipped = Spool(tmp_path).read(10)
    assert rows == [] and skipped == 1
    assert cursor.offset == (tmp_path / "capture-2026-10-24.jsonl").stat().st_size  # committing it never gets stuck


def test_an_unparseable_line_is_skipped_counted_and_passed(tmp_path):
    (tmp_path / "capture-2026-10-24.jsonl").write_text(line("a") + "not json at all\n" + "[1, 2]\n" + line("b"))
    rows, cursor, skipped = Spool(tmp_path).read(10)
    assert ids(rows) == ["a", "b"] and skipped == 2
    assert cursor.offset == (tmp_path / "capture-2026-10-24.jsonl").stat().st_size


def test_skipped_lines_make_progress_even_with_no_row(tmp_path):
    (tmp_path / "capture-2026-10-24.jsonl").write_text("garbage\n")
    s = Spool(tmp_path)
    rows, cursor, skipped = s.read(10)
    assert (rows, skipped) == ([], 1) and cursor.offset == len("garbage\n")
    s.commit(cursor)
    assert s.read(10) == ([], cursor, 0)


def test_max_bytes_applies_to_the_ingestion_line(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b"])
    size = len(json.dumps(to_ingest(row("a"), instance_id(tmp_path)), ensure_ascii=False).encode()) + 1
    rows, _, skipped = Spool(tmp_path).read(10, max_bytes=size)
    assert ids(rows) == ["a"] and skipped == 0  # one line fits exactly; the next opens the next batch
    rows, _, skipped = Spool(tmp_path).read(10, max_bytes=size - 1)
    assert rows == [] and skipped == 2


def test_a_batch_is_capped_at_max_bytes_not_only_a_line(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b", "c", "d", "e"])
    size = len(json.dumps(to_ingest(row("a"), "x"), ensure_ascii=False).encode()) + 1  # ids have the same length
    cap = size * 2 + size // 2  # two lines fit, not three
    spool = Spool(tmp_path)
    rows, cursor, skipped = spool.read(10, max_bytes=cap)
    assert ids(rows) == ["a", "b"] and skipped == 0
    assert cursor.offset == len(line("a").encode()) * 2
    spool.commit(cursor)
    rows, cursor, _ = Spool(tmp_path).read(10, max_bytes=cap)
    assert ids(rows) == ["c", "d"]
    spool.commit(cursor)
    assert ids(Spool(tmp_path).read(10, max_bytes=cap)[0]) == ["e"]


def test_the_batch_cap_applies_across_files_and_an_oversized_line_still_skips(tmp_path):
    write(tmp_path, "capture-2026-10-23.jsonl", ["a"])
    (tmp_path / "capture-2026-10-24.jsonl").write_text(line("big", prompt="x" * 5000) + line("b"))
    size = len(json.dumps(to_ingest(row("a"), "x"), ensure_ascii=False).encode()) + 1
    rows, cursor, skipped = Spool(tmp_path).read(10, max_bytes=size + size // 2)
    assert ids(rows) == ["a"] and skipped == 1  # "big" alone is too large; "b" would not fit after "a"
    assert cursor == Cursor("capture-2026-10-24.jsonl", len(line("big", prompt="x" * 5000).encode()))


def test_a_dead_partial_tail_in_an_older_file_does_not_block_the_next_file(tmp_path):
    (tmp_path / "capture-2026-10-23.jsonl").write_text(line("a") + '{"id": "x", "pro')
    write(tmp_path, "capture-2026-10-24.jsonl", ["b"])
    rows, _, skipped = Spool(tmp_path).read(10)
    assert ids(rows) == ["a", "b"] and skipped == 1


def test_rows_carry_the_instance_id(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    rows, _, _ = Spool(tmp_path).read(10)
    assert rows[0]["instance_id"] == instance_id(tmp_path) and rows[0]["v"] == 1


def test_no_line_content_in_the_logs(tmp_path, caplog, capsys):
    (tmp_path / "capture-2026-10-24.jsonl").write_text("SECRET-PROMPT-TEXT\n")
    Spool(tmp_path).read(10)
    assert "SECRET-PROMPT-TEXT" not in caplog.text
    out = capsys.readouterr()
    assert "SECRET-PROMPT-TEXT" not in out.out + out.err


# --- only what was captured while sending was on --------------------------------------------------------------------

PRE_080 = {k: v for k, v in row("old", send=False).items() if k != "terms_version"}  # a line from before 0.8.0


def test_lines_not_marked_for_sending_are_passed_silently(tmp_path):
    text = line("off", send=False) + json.dumps(PRE_080) + "\n" + line("a") + line("off2", send=False)
    (tmp_path / "capture-2026-10-24.jsonl").write_text(text)
    rows, cursor, skipped = Spool(tmp_path).read(10)
    assert ids(rows) == ["a"] and skipped == 0  # passed, not counted
    assert cursor.offset == (tmp_path / "capture-2026-10-24.jsonl").stat().st_size


def test_only_a_strict_true_marks_a_line(tmp_path):
    lines = [json.dumps(row("t", send=False) | {"send": v}) + "\n" for v in ("true", 1, False, None)]
    (tmp_path / "capture-2026-10-24.jsonl").write_text("".join(lines))
    assert Spool(tmp_path).read(10)[0::2] == ([], 0)


def test_a_marked_line_without_terms_version_is_skipped_and_counted(tmp_path):
    marked = {k: v for k, v in row("x").items() if k != "terms_version"}
    (tmp_path / "capture-2026-10-24.jsonl").write_text(json.dumps(marked) + "\n" + line("a"))
    rows, _, skipped = Spool(tmp_path).read(10)
    assert ids(rows) == ["a"] and skipped == 1


def test_unmarked_lines_only_are_committed_without_a_post_or_a_count(tmp_path):
    path = tmp_path / "capture-2026-10-24.jsonl"
    path.write_text(line("off", send=False) + json.dumps(PRE_080) + "\n")
    calls = []
    s = sender(tmp_path, codes=[], calls=calls)
    before = counter("tantive_capture_send_rejected_total")
    assert s.run_once() == "empty"
    assert calls == [] and counter("tantive_capture_send_rejected_total") == before
    assert json.loads((tmp_path / ".send-cursor").read_text())["offset"] == path.stat().st_size


def sent_prompts(calls) -> list[list[str]]:
    return [[json.loads(x)["prompt"] for x in body.decode().splitlines()] for _, _, body in calls]


def capture(directory: Path, send: bool, *prompts: str) -> None:
    c = make(directory, send=send)
    for p in prompts:
        offer(c, prompt=p)
    c.close()


def test_what_was_captured_before_sending_was_on_is_never_sent(tmp_path):
    capture(tmp_path, False, "before-1", "before-2")
    capture(tmp_path, True, "after")
    calls = []
    s = sender(tmp_path, codes=[200], calls=calls)
    before = counter("tantive_capture_send_rejected_total")
    assert s.run_once() == "sent" and s.run_once() == "empty"
    assert sent_prompts(calls) == [["after"]]
    assert counter("tantive_capture_send_rejected_total") == before


def test_on_off_on_never_sends_the_off_period(tmp_path):
    calls = []
    s = sender(tmp_path, codes=[200, 200], calls=calls)
    capture(tmp_path, True, "on-1")
    assert s.run_once() == "sent"
    capture(tmp_path, False, "off-1", "off-2")  # the service restarted with sending off
    capture(tmp_path, True, "on-2")  # and on again
    assert s.run_once() == "sent" and s.run_once() == "empty"
    assert sent_prompts(calls) == [["on-1"], ["on-2"]]


def test_a_pre_080_line_is_passed_silently(tmp_path):
    (tmp_path / "capture-2026-10-24.jsonl").write_text(json.dumps(PRE_080) + "\n" + line("a"))
    calls = []
    s = sender(tmp_path, codes=[200], calls=calls)
    before = counter("tantive_capture_send_rejected_total")
    assert s.run_once() == "sent"
    assert sent_ids(calls[0][2]) == ["a"] and counter("tantive_capture_send_rejected_total") == before


def test_an_offset_beyond_the_file_size_restarts_that_file(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b"])
    s = Spool(tmp_path)
    s.commit(Cursor("capture-2026-10-24.jsonl", 10_000))  # the file was replaced by a shorter one
    rows, cursor, _ = s.read(10)
    assert ids(rows) == ["a", "b"]  # the ingestion dedupes on id
    assert cursor.offset == (tmp_path / "capture-2026-10-24.jsonl").stat().st_size


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nan_or_infinity_is_skipped_and_counted(tmp_path, value):
    bad = json.dumps(row("bad", score=value))  # what json.dumps writes by default: NaN, Infinity
    (tmp_path / "capture-2026-10-24.jsonl").write_text(line("a") + bad + "\n" + line("b"))
    rows, _, skipped = Spool(tmp_path).read(10)
    assert ids(rows) == ["a", "b"] and skipped == 1


def test_pending_counts_the_bytes_after_the_cursor(tmp_path):
    write(tmp_path, "capture-2026-10-23.jsonl", ["a"])
    write(tmp_path, "capture-2026-10-24.jsonl", ["b", "c"])
    s = Spool(tmp_path)
    total = sum(p.stat().st_size for p in tmp_path.glob("capture-*.jsonl"))
    assert s.pending() == total
    s.commit(Cursor("capture-2026-10-24.jsonl", len(line("b").encode())))
    assert s.pending() == len(line("c").encode())  # older files are behind the cursor
    s.commit(Cursor("capture-2026-10-24.jsonl", 10_000))
    assert s.pending() == (tmp_path / "capture-2026-10-24.jsonl").stat().st_size  # restarted at 0


def test_the_pending_gauge_falls_after_a_successful_send(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b", "c"])
    size = (tmp_path / "capture-2026-10-24.jsonl").stat().st_size
    s = sender(tmp_path, codes=[503, 200, 200], batch=2)
    assert s.run_once() == "failed"
    assert counter("tantive_capture_send_pending_bytes") == size
    assert s.run_once() == "sent"
    assert counter("tantive_capture_send_pending_bytes") == len(line("c").encode())
    assert s.run_once() == "sent"
    assert counter("tantive_capture_send_pending_bytes") == 0


# --- the sender: one POST per batch, the cursor moves only on 2xx or a definitive rejection --------------------------


def sender(directory: Path, codes, calls=None, token=None, batch=100, sleep=lambda s: None) -> Sender:
    codes = iter(codes)
    calls = [] if calls is None else calls

    def post(url, token, body):
        calls.append((url, token, body))
        return next(codes)

    return Sender(
        str(directory),
        url="http://127.0.0.1:9/ingest",
        token=token,
        batch=batch,
        interval=1,
        post=post,
        sleep=sleep,
        start=False,
    )


@pytest.fixture
def logs():
    """JSON log lines on stdout, as in the service (read with capsys)."""
    configure_logging(False)


def sent_ids(body: bytes) -> list[str]:
    return [json.loads(x)["id"] for x in body.decode().splitlines()]


def test_a_batch_is_posted_as_ndjson_then_committed(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b"])
    calls = []
    s = sender(tmp_path, codes=[202], calls=calls)
    before = counter("tantive_capture_sent_total")
    assert s.run_once() == "sent"
    url, token, body = calls[0]
    assert url == "http://127.0.0.1:9/ingest" and token is None
    assert sent_ids(body) == ["a", "b"]
    assert body.endswith(b"\n") and all(json.loads(x)["v"] == 1 for x in body.decode().splitlines())
    assert counter("tantive_capture_sent_total") == before + 2
    assert s.run_once() == "empty"
    assert len(calls) == 1  # nothing to send: no request at all


def test_the_body_is_encoded_like_the_spool_sizes_it(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"], big="é" * 10)
    calls = []
    sender(tmp_path, codes=[200], calls=calls).run_once()
    body = calls[0][2]
    assert "é".encode() * 10 in body  # UTF-8, not \u escapes
    (tmp_path / ".send-cursor").unlink()  # read the same line again
    assert ids(Spool(tmp_path).read(10, max_bytes=len(body))[0]) == ["a"]  # the spool sized it at exactly this
    assert Spool(tmp_path).read(10, max_bytes=len(body) - 1)[0::2] == ([], 1)


def test_failure_keeps_the_cursor(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    calls = []
    s = sender(tmp_path, codes=[503, 0, 429, 200], calls=calls)
    before = counter("tantive_capture_send_failed_total")
    assert [s.run_once() for _ in range(4)] == ["failed", "failed", "failed", "sent"]
    assert len({c[2] for c in calls}) == 1  # the same batch every time
    assert counter("tantive_capture_send_failed_total") == before + 3


def test_a_rejected_batch_is_skipped(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    s = sender(tmp_path, codes=[400])
    before = counter("tantive_capture_send_rejected_total")
    assert s.run_once() == "rejected" and s.run_once() == "empty"
    assert counter("tantive_capture_send_rejected_total") == before + 1


def test_a_413_is_a_rejection_too(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b"])
    s = sender(tmp_path, codes=[413])
    before = counter("tantive_capture_send_rejected_total")
    assert s.run_once() == "rejected" and s.run_once() == "empty"
    assert counter("tantive_capture_send_rejected_total") == before + 2


def test_restart_resumes_at_the_cursor(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b", "c"])
    sender(tmp_path, codes=[200], batch=2).run_once()
    calls = []
    sender(tmp_path, codes=[200], calls=calls).run_once()
    assert sent_ids(calls[0][2]) == ["c"]


def test_only_skipped_lines_are_committed_and_counted_as_rejected(tmp_path):
    (tmp_path / "capture-2026-10-24.jsonl").write_text("garbage\nmore garbage\n")
    calls = []
    s = sender(tmp_path, codes=[], calls=calls)
    before = counter("tantive_capture_send_rejected_total")
    assert s.run_once() == "rejected" and s.run_once() == "empty"
    assert calls == [] and counter("tantive_capture_send_rejected_total") == before + 2


def test_skipped_lines_beside_rows_are_counted_once_even_after_a_failure(tmp_path):
    (tmp_path / "capture-2026-10-24.jsonl").write_text("garbage\n" + line("a"))
    calls = []
    s = sender(tmp_path, codes=[503, 503, 200], calls=calls)
    before = counter("tantive_capture_send_rejected_total")
    assert [s.run_once() for _ in range(3)] == ["failed", "failed", "sent"]
    assert sent_ids(calls[-1][2]) == ["a"]
    assert counter("tantive_capture_send_rejected_total") == before + 1  # not once per attempt


def test_the_token_goes_in_the_header_only(tmp_path, capsys, logs):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    calls = []
    sender(tmp_path, codes=[0, 200], calls=calls, token="s3cret").run_once()
    assert calls[0][1] == "s3cret" and "s3cret" not in calls[0][2].decode() and "s3cret" not in capsys.readouterr().out


def test_failure_logs_one_line_per_outage_with_the_code_only(tmp_path, capsys, logs):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"], big="SECRET-PROMPT-TEXT")
    s = sender(tmp_path, codes=[503, 0, 503, 200, 400], calls=[], token="s3cret")
    [s.run_once() for _ in range(4)]
    write(tmp_path, "capture-2026-10-24-001.jsonl", ["b"], big="SECRET-PROMPT-TEXT")
    assert s.run_once() == "rejected"
    events = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [(e["event"], e["status"]) for e in events] == [("capture_send_failed", 503), ("capture_send_rejected", 400)]
    for e in events:
        assert set(e) == {"time", "level", "event", "status"}
    text = json.dumps(events)
    assert "SECRET-PROMPT-TEXT" not in text and "s3cret" not in text and "127.0.0.1" not in text


def test_a_new_outage_logs_again(tmp_path, capsys, logs):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    s = sender(tmp_path, codes=[503, 200, 502])
    s.run_once(), s.run_once()
    write(tmp_path, "capture-2026-10-24-001.jsonl", ["b"])
    s.run_once()
    events = [json.loads(x)["event"] for x in capsys.readouterr().out.splitlines()]
    assert events == ["capture_send_failed", "capture_send_failed"]


def test_the_instance_id_is_the_directory_one(tmp_path):
    assert sender(tmp_path, codes=[]).instance == instance_id(tmp_path)


def run_loop(s: Sender, waits: list[float], results: list[str], stop_after: int) -> None:
    """Drive the loop in the foreground: the fake sleep records each wait and closes the sender after `stop_after`."""
    s.run_once = lambda: results.pop(0)

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) >= stop_after:
            s.close()

    s._wait = sleep
    s._run()


def test_the_wait_doubles_after_each_failure_up_to_600_then_resets(tmp_path):
    waits: list[float] = []
    results = ["empty", "sent"] + ["failed"] * 11 + ["sent", "failed", "rejected", "empty"]
    s = sender(tmp_path, codes=[])
    run_loop(s, waits, results, stop_after=len(results))
    assert waits == [1, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 600, 600, 1, 2, 1, 1]


def test_the_injected_sleep_is_used_by_the_loop(tmp_path):
    waits: list[float] = []
    s = sender(tmp_path, codes=[], sleep=lambda seconds: (waits.append(seconds), s.close()))
    s._run()  # one run_once on an empty directory, one wait, then the fake sleep closes it
    assert waits == [1]


def test_the_loop_survives_an_unexpected_error(tmp_path, capsys, logs):
    waits: list[float] = []
    s = sender(tmp_path, codes=[])
    outcomes = iter([RuntimeError("SECRET-PROMPT-TEXT"), "sent"])

    def boom():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            s.close()

    s.run_once, s._wait = boom, sleep
    before = counter("tantive_capture_send_failed_total")
    s._run()
    assert waits == [2, 1]  # the error backs off like a failure
    assert counter("tantive_capture_send_failed_total") == before + 1
    [event] = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert (event["event"], event["error"]) == ("capture_send_error", "RuntimeError")
    assert "SECRET-PROMPT-TEXT" not in json.dumps(event)


def drive(s: Sender, events: list, waits_before_stop: int) -> None:
    """Run the real loop in the foreground; the fake sleep records each wait and closes after a few."""

    def sleep(seconds):
        events.append(("wait", seconds))
        if sum(1 for e in events if e[0] == "wait") >= waits_before_stop:
            s.close()

    s._wait = sleep
    s._run()


def recording_sender(tmp_path, events: list, codes, batch: int) -> Sender:
    codes = iter(codes)

    def post(url, token, body):
        events.append(("post", sent_ids(body)))
        return next(codes)

    return Sender(
        str(tmp_path), url="http://127.0.0.1:9/ingest", token=None, batch=batch, interval=1, post=post, start=False
    )


def test_a_backlog_drains_without_waiting_between_full_batches(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b", "c", "d", "e"])
    events: list = []
    s = recording_sender(tmp_path, events, codes=[200, 200, 200], batch=2)
    drive(s, events, waits_before_stop=2)
    assert events == [
        ("post", ["a", "b"]),  # full: the next batch at once
        ("post", ["c", "d"]),  # full: the next batch at once
        ("post", ["e"]),  # not full: the spool is drained
        ("wait", 1),
        ("wait", 1),  # after the empty result
    ]


def test_a_full_rejected_batch_does_not_wait_either(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b", "c"])
    events: list = []
    s = recording_sender(tmp_path, events, codes=[400, 200], batch=2)
    drive(s, events, waits_before_stop=1)
    assert events == [("post", ["a", "b"]), ("post", ["c"]), ("wait", 1)]


def test_a_batch_stopped_by_the_byte_cap_counts_as_full(tmp_path):
    big = "x" * 400_000  # two lines fit in 1 MiB, not three
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b", "c"], big=big)
    events: list = []
    s = recording_sender(tmp_path, events, codes=[200, 200], batch=100)
    drive(s, events, waits_before_stop=1)
    assert events == [("post", ["a", "b"]), ("post", ["c"]), ("wait", 1)]


def test_a_failure_after_a_full_batch_backs_off(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b", "c"])
    events: list = []
    s = recording_sender(tmp_path, events, codes=[200, 503, 200], batch=2)
    drive(s, events, waits_before_stop=2)
    assert events == [("post", ["a", "b"]), ("post", ["c"]), ("wait", 2), ("post", ["c"]), ("wait", 1)]


def test_the_spool_says_when_a_read_stopped_before_the_end(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a", "b"])
    spool = Spool(tmp_path)
    spool.read(2)
    assert spool.full
    spool.read(3)
    assert not spool.full
    size = len(json.dumps(to_ingest(row("a"), instance_id(tmp_path)), ensure_ascii=False).encode()) + 1
    spool.read(10, max_bytes=size)
    assert spool.full


@pytest.mark.parametrize("between", ["empty", "skipped only"])
def test_any_result_but_a_failure_ends_the_outage(tmp_path, capsys, logs, between):
    write(tmp_path, "capture-2026-10-23.jsonl", ["a"])
    s = sender(tmp_path, codes=[503, 503])
    assert s.run_once() == "failed"
    (tmp_path / "capture-2026-10-23.jsonl").unlink()  # purged by the retention
    if between == "empty":
        assert s.run_once() == "empty"
    else:
        (tmp_path / "capture-2026-10-24.jsonl").write_text("garbage\n")
        assert s.run_once() == "rejected"
    write(tmp_path, "capture-2026-10-25.jsonl", ["b"])
    assert s.run_once() == "failed"
    failed = [x for x in capsys.readouterr().out.splitlines() if json.loads(x)["event"] == "capture_send_failed"]
    assert len(failed) == 2  # the second outage is logged too


def test_close_stops_the_thread_promptly(tmp_path):
    write(tmp_path, "capture-2026-10-24.jsonl", ["a"])
    calls = []
    posted = threading.Event()

    def post(url, token, body):
        calls.append(body)
        posted.set()
        return 200

    s = Sender(str(tmp_path), url="http://127.0.0.1:9/ingest", token=None, batch=10, interval=3600, post=post)
    assert posted.wait(5) and s._thread.name == "s1d1-send" and s._thread.daemon
    started = time.monotonic()
    s.close()
    assert time.monotonic() - started < 1 and not s._thread.is_alive()
    assert len(calls) == 1


# --- post_ndjson against a real local server ------------------------------------------------------------------------


class _Recorder(http.server.BaseHTTPRequestHandler):
    seen: list[tuple[str, dict, bytes]] = []
    status = 202
    location: str | None = None

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).seen.append(("POST", dict(self.headers), body))
        self.send_response(type(self).status)
        if type(self).location:
            self.send_header("Location", type(self).location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        type(self).seen.append(("GET", dict(self.headers), b""))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def serve(status=202, location=None):
    _Recorder.seen, _Recorder.status, _Recorder.location = [], status, location
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/ingest"


def test_post_ndjson_against_a_local_server():
    server, url = serve(202)
    try:
        body = b'{"v": 1}\n'
        assert post_ndjson(url, "t", body) == 202
        assert post_ndjson(url, None, body) == 202
    finally:
        server.shutdown()
        server.server_close()
    (_, with_token, received), (_, without_token, _) = _Recorder.seen
    for headers in (with_token, without_token):
        assert headers["Content-Type"] == "application/x-ndjson"
        assert headers["User-Agent"] == f"s1d1-tantive/{__version__}"
    assert with_token["Authorization"] == "Bearer t"
    assert "Authorization" not in without_token
    assert received == body


def test_post_ndjson_returns_the_http_error_code():
    server, url = serve(503)
    try:
        assert post_ndjson(url, None, b"{}\n") == 503
    finally:
        server.shutdown()
        server.server_close()


def test_post_ndjson_does_not_follow_a_redirect():
    server, url = serve(302, location="/elsewhere")
    try:
        assert post_ndjson(url, "t", b"{}\n") == 302  # a failure: the token never goes to another address
    finally:
        server.shutdown()
        server.server_close()
    assert [method for method, _, _ in _Recorder.seen] == ["POST"]


def test_post_ndjson_network_errors_are_zero():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    port = server.server_address[1]
    server.server_close()  # nothing listens there any more
    assert post_ndjson(f"http://127.0.0.1:{port}/ingest", None, b"{}\n", timeout=2) == 0
    assert post_ndjson("http://[not a url", None, b"{}\n") == 0
