"""JSONL capture of decisions: off by default, one writer thread, never the request path, text nowhere else."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from prometheus_client import REGISTRY

from tantive.capture import Capture
from tantive.config import ConfigError
from tantive.model import ModelInfo
from tantive.telemetry import metrics
from tantive.tiers import TRAINING_SET
from tests.conftest import outputs

INFO = ModelInfo("fake", revision="abc1234", sigma=0.10, sigma_source="default", onnx_file="fake.onnx")
DAY = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def make(tmp_path, **kw) -> Capture:
    kw.setdefault("clock", lambda: DAY)
    return Capture(str(tmp_path), info=INFO, native=TRAINING_SET, **kw)


def offer(c: Capture, prompt="how do I fix this bug?", score=0.62) -> None:
    c.offer(prompt, "/v1/systemone", "systemone", outputs(score=score), TRAINING_SET.tier_for(score), 12.34)


def lines(tmp_path) -> list[dict]:
    return [json.loads(x) for f in sorted(tmp_path.glob("capture-*.jsonl")) for x in f.read_text().splitlines()]


def counter(name: str) -> float:
    metrics()
    return sum(x.value for m in REGISTRY.collect() for x in m.samples if x.name == name)


def test_a_line_has_the_documented_fields(tmp_path):
    c = make(tmp_path)
    offer(c)
    c.close()
    [row] = lines(tmp_path)
    assert list(row) == [
        "time", "id", "prompt", "format", "route", "tier", "score", "task", "needs_tools", "confidence",
        "pieces", "truncated", "prompt_tokens", "latency_ms", "variant", "revision", "service_version",
    ]  # fmt: skip
    assert row["time"] == "2026-10-21T12:00:00.000Z"
    assert row["prompt"] == "how do I fix this bug?"
    assert (row["format"], row["tier"], row["task"], row["variant"], row["revision"]) == (
        "systemone", "MEDIUM", "coding_debugging", "fake", "abc1234",
    )  # fmt: skip
    assert 0 <= row["confidence"] <= 1
    assert row["latency_ms"] == 12.3
    print(json.dumps(row))


def test_one_file_per_utc_day(tmp_path):
    now = [DAY]
    c = make(tmp_path, clock=lambda: now[0])
    offer(c)
    now[0] = DAY + timedelta(days=1)
    offer(c)
    c.close()
    assert [f.name for f in sorted(tmp_path.iterdir())] == ["capture-2026-10-21.jsonl", "capture-2026-10-22.jsonl"]


def test_rotation_by_size(tmp_path):
    c = make(tmp_path, max_bytes=600)
    for _ in range(5):
        offer(c)
    c.close()
    names = {f.name for f in tmp_path.iterdir()}
    assert {"capture-2026-10-21.jsonl", "capture-2026-10-21-001.jsonl"} <= names
    assert len(lines(tmp_path)) == 5
    assert all(f.stat().st_size <= 600 for f in tmp_path.iterdir())


def test_a_restart_appends_to_the_last_file_of_the_day(tmp_path):
    for _ in range(2):
        c = make(tmp_path)
        offer(c)
        c.close()
    assert len(list(tmp_path.iterdir())) == 1
    assert len(lines(tmp_path)) == 2


def test_sample_zero_keeps_nothing_and_one_keeps_everything(tmp_path):
    c = make(tmp_path / "none", sample=0.0)
    for _ in range(20):
        offer(c)
    c.close()
    assert lines(tmp_path / "none") == []
    c = make(tmp_path / "all", sample=1.0)
    for _ in range(20):
        offer(c)
    c.close()
    assert len(lines(tmp_path / "all")) == 20


def test_sample_is_a_random_draw_per_decision(tmp_path):
    draws = iter([0.1, 0.9, 0.4, 0.6])
    c = make(tmp_path, sample=0.5, rng=lambda: next(draws))
    for _ in range(4):
        offer(c)
    c.close()
    assert len(lines(tmp_path)) == 2


def test_min_margin_keeps_only_doubtful_scores(tmp_path):
    c = make(tmp_path, min_margin=0.05)
    offer(c, score=0.62)  # 0.08 from the 0.7 bound, 0.22 from 0.4: clear
    offer(c, score=0.66)  # 0.04 from 0.7: doubtful
    offer(c, score=0.41)  # 0.01 from 0.4: doubtful
    c.close()
    assert [r["score"] for r in lines(tmp_path)] == [0.66, 0.41]


def test_retention_removes_old_files_at_start_and_once_a_day(tmp_path):
    (tmp_path / "capture-2026-09-01.jsonl").write_text("{}\n")
    (tmp_path / "capture-2026-09-01-001.jsonl").write_text("{}\n")
    (tmp_path / "capture-2026-10-20.jsonl").write_text("{}\n")
    (tmp_path / "notes.txt").write_text("keep")
    now = [DAY]
    c = make(tmp_path, retention_days=7, clock=lambda: now[0])
    offer(c)
    c.flush()
    assert sorted(f.name for f in tmp_path.iterdir()) == [
        "capture-2026-10-20.jsonl",
        "capture-2026-10-21.jsonl",
        "notes.txt",
    ]
    now[0] = DAY + timedelta(days=7)  # 10-20 is now 8 days old
    offer(c)
    c.close()
    assert not (tmp_path / "capture-2026-10-20.jsonl").exists()
    assert (tmp_path / "notes.txt").exists()


def test_a_full_queue_drops_and_counts(tmp_path):
    c = make(tmp_path, queue_size=3, start=False)  # no writer: nothing drains
    before = counter("tantive_capture_dropped_total")
    for _ in range(5):
        offer(c)
    assert counter("tantive_capture_dropped_total") - before == 2
    c.start()
    c.close()
    assert len(lines(tmp_path)) == 3


def test_written_counter(tmp_path):
    before = counter("tantive_capture_written_total")
    c = make(tmp_path)
    offer(c)
    offer(c)
    c.close()
    assert counter("tantive_capture_written_total") - before == 2


def test_the_directory_is_created(tmp_path):
    c = make(tmp_path / "a" / "b")
    c.close()
    assert (tmp_path / "a" / "b").is_dir()


def test_an_unwritable_directory_is_a_startup_error(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(ConfigError, match="S1D1_CAPTURE_DIR"):
        make(f)


# --- through the app ---------------------------------------------------------------------------------------------


async def test_every_adapter_path_captures_and_the_text_stays_out_of_stdout(
    fake_model, settings_factory, tmp_path, capsys
):
    from tantive.app import create_app
    from tantive.telemetry import configure_logging
    from tests.conftest import _client

    configure_logging(False)
    app = create_app(settings_factory(S1D1_CAPTURE_DIR=str(tmp_path), S1D1_FORMATS="systemone,classify"), fake_model)
    async for c in _client(app):
        r = await c.post("/classify", json={"input": "my secret question about tax"})
        assert r.status_code == 200, r.text
        info = (await c.get("/info")).json()
    out = capsys.readouterr().out
    assert "secret question" not in out
    [row] = lines(tmp_path)
    assert row["prompt"] == "my secret question about tax"
    assert row["format"] == "classify"
    assert info["capture"] == {
        "enabled": True, "dir": str(tmp_path), "sample": 1.0, "min_margin": None, "retention_days": None,
    }  # fmt: skip


async def test_info_when_capture_is_off(client):
    assert (await client.get("/info")).json()["capture"] == {
        "enabled": False, "dir": None, "sample": None, "min_margin": None, "retention_days": None,
    }  # fmt: skip
