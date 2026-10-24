"""The local spool over the capture files: stable instance id, v1 ingestion lines, cursor, ordering, skipped lines."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from tantive.send import Cursor, Spool, instance_id, to_ingest


def row(ident: str, **extra) -> dict:
    return {
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


def line(ident: str, **extra) -> str:
    return json.dumps(row(ident, **extra), ensure_ascii=False) + "\n"


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


def test_to_ingest_defaults_missing_keys():
    out = to_ingest({"id": "i"}, "inst")
    assert out["terms_version"] == "v1"
    assert out["pred_tier"] is None and out["model_revision"] is None and out["prompt"] is None


def test_to_ingest_drops_the_local_only_fields():
    out = to_ingest(row("a"), "inst")
    assert not {"route", "pieces", "truncated", "prompt_tokens", "latency_ms", "service_version"} & set(out)


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
    assert ids(rows) == ["a", "b"] and skipped == 0
    rows, _, skipped = Spool(tmp_path).read(10, max_bytes=size - 1)
    assert rows == [] and skipped == 2


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
