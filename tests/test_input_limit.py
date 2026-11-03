"""The input limit over HTTP (docs/40 § 4): no 413 on the text, the body cap as a setting, the effective limit in
the `ready` line, `/info` and `/metrics`, and the 503 left to the load."""

from __future__ import annotations

import asyncio
import json

import pytest

from tantive.app import create_app
from tantive.engine import CostModel, Engine
from tantive.telemetry import configure_logging
from tests.conftest import _client, outputs

QUESTION = {"tier": {"type": "choice", "criteria": {"SIMPLE": "", "COMPLEX": ""}}}
MIB = 1024 * 1024


def events(out: str, name: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if f'"event": "{name}"' in line]


@pytest.fixture
def cost(monkeypatch):
    """The warm-up returns this cost model instead of timing the fake model."""

    def use(fixed_ms: float, per_token_ms: float) -> None:
        monkeypatch.setattr(Engine, "warmup", lambda self: CostModel(fixed_ms, per_token_ms))

    return use


async def started(app) -> None:
    async with app.router.lifespan_context(app):
        pass


# --- the text is cut, not refused ------------------------------------------------------------------------------------


async def test_a_text_of_60000_characters_is_classified(client):
    r = await client.post("/v1/systemone", json={"state": "word " * 12_000, "questions": QUESTION})
    assert r.status_code == 200  # 413 in 0.8.1


async def test_a_litellm_state_with_a_long_quoted_context_is_classified(client, fake_model):
    state = "context " * 20_000 + "\nClassify this message:\nhello"
    seen = []
    fake_model.on_encode = seen.append
    r = await client.post("/v1/systemone", json={"state": state, "questions": QUESTION})
    assert r.status_code == 200 and seen == ["hello"]


async def test_batch_items_follow_the_same_rule(client):
    r = await client.post("/v1/systemone/batch", json={"states": ["a", "x" * 60_000], "questions": QUESTION})
    assert r.status_code == 200 and all("answers" in x for x in r.json()["results"])


async def test_a_body_over_2_mib_is_still_413_by_default(client):
    body = json.dumps({"state": "x" * (2 * MIB), "questions": QUESTION}).encode()
    r = await client.post("/v1/systemone", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["message"] == f"request body exceeds {2 * MIB} bytes"


# --- S1D1_MAX_BODY_BYTES ---------------------------------------------------------------------------------------------


async def _chunks(data: bytes, size: int = 64 * 1024):
    for i in range(0, len(data), size):
        yield data[i : i + size]


@pytest.mark.parametrize("streamed", [False, True])
async def test_the_body_cap_is_a_setting(fake_model, settings_factory, streamed):
    app = create_app(settings_factory(S1D1_MAX_BODY_BYTES="4194304"), fake_model)
    ok = json.dumps({"state": "x" * (3 * MIB), "questions": QUESTION}).encode()
    too_big = b"x" * (5 * MIB)
    async for c in _client(app):
        for body, status in ((ok, 200), (too_big, 413)):
            content = _chunks(body) if streamed else body
            r = await c.post("/v1/systemone", content=content, headers={"content-type": "application/json"})
            assert r.status_code == status
        assert (await c.get("/info")).json()["max_body_bytes"] == 4 * MIB


async def test_the_body_cap_is_in_the_ready_line(fake_model, settings_factory, capsys):
    configure_logging(False)
    await started(create_app(settings_factory(S1D1_MAX_BODY_BYTES="65536"), fake_model))
    (ready,) = events(capsys.readouterr().out, "ready")
    assert ready["max_body_bytes"] == 65_536


async def test_default_body_cap_in_info(client):
    assert (await client.get("/info")).json()["max_body_bytes"] == 2 * MIB


# --- the limit in the ready line, /info and /metrics -----------------------------------------------------------------


async def test_auto_in_ready_info_and_metrics(fake_model, settings_factory, cost, capsys):
    configure_logging(False)
    cost(2.0, 1.39)
    app = create_app(settings_factory(S1D1_MAX_INPUT_TOKENS="auto"), fake_model)
    async for c in _client(app):
        info = (await c.get("/info")).json()["max_input_tokens"]
        text = (await c.get("/metrics")).text
    out = capsys.readouterr().out
    (ready,) = events(out, "ready")
    assert (ready["max_input_tokens"], ready["max_input_tokens_source"], ready["predicted_ms_limit"]) == (
        256,
        "auto",
        358,
    )
    assert info == {"setting": "auto", "effective": 256, "source": "auto", "predicted_ms": 357.84}
    gauge = [line for line in text.splitlines() if line.startswith("tantive_max_input_tokens{")]
    assert any('source="auto"' in line and line.endswith(" 256.0") for line in gauge)
    assert not events(out, "capacity_warning") and not events(out, "input_limit_capped")


async def test_an_empty_limit_is_the_window_in_info(client):
    info = (await client.get("/info")).json()["max_input_tokens"]
    assert (info["setting"], info["effective"], info["source"]) == (None, 1024, "window")


async def test_a_capped_integer_logs_both_values(fake_model, settings_factory, cost, capsys):
    configure_logging(False)
    cost(1.0, 0.01)
    await started(create_app(settings_factory(S1D1_MAX_INPUT_TOKENS="5000"), fake_model))
    out = capsys.readouterr().out
    (capped,) = events(out, "input_limit_capped")
    assert (capped["requested"], capped["effective"], capped["level"]) == (5000, 1024, "warning")
    (ready,) = events(out, "ready")
    assert (ready["max_input_tokens"], ready["max_input_tokens_source"]) == (1024, "user")


async def test_an_integer_beyond_the_deadline_warns(fake_model, settings_factory, cost, capsys):
    configure_logging(False)
    cost(10.0, 2.0)
    await started(create_app(settings_factory(S1D1_MAX_INPUT_TOKENS="512"), fake_model))
    (warning,) = events(capsys.readouterr().out, "capacity_warning")
    assert (warning["max_input_tokens"], warning["predicted_ms_limit"], warning["deadline_ms"]) == (512, 1034, 800)


async def test_auto_at_the_floor_warns(fake_model, settings_factory, cost, capsys):
    configure_logging(False)
    cost(10.0, 4.0)
    await started(create_app(settings_factory(S1D1_MAX_INPUT_TOKENS="auto"), fake_model))
    (warning,) = events(capsys.readouterr().out, "capacity_warning")
    assert (warning["max_input_tokens"], warning["predicted_ms_limit"]) == (128, 522)


# --- the 503 is left to the load -------------------------------------------------------------------------------------


async def test_idle_server_with_auto_never_sheds_a_2_mib_text(fake_model, settings_factory, cost):
    cost(10.0, 4.0)  # a full window: 4 106 ms, five times the deadline
    body = {"state": "word " * (2 * MIB // 5), "questions": QUESTION}
    big_bodies = {"S1D1_MAX_BODY_BYTES": "4194304"}
    async for c in _client(create_app(settings_factory(S1D1_MAX_INPUT_TOKENS="auto", **big_bodies), fake_model)):
        r = await c.post("/v1/systemone", json=body)
        assert r.status_code == 200
    async for c in _client(create_app(settings_factory(**big_bodies), fake_model)):
        assert (await c.post("/v1/systemone", json=body)).status_code == 503  # the window, as in 0.8.1


async def test_a_full_queue_still_sheds_with_retry_after(slow_fake, settings_factory, cost):
    cost(1.0, 0.01)
    model = slow_fake(fixed_s=0.2)
    app = create_app(settings_factory(S1D1_MAX_INPUT_TOKENS="auto", S1D1_MAX_QUEUE="1"), model)
    async for c in _client(app):
        responses = await asyncio.gather(
            *(c.post("/v1/systemone", json={"state": f"hello {i}", "questions": QUESTION}) for i in range(3))
        )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 200, 503]
    assert next(r for r in responses if r.status_code == 503).headers["Retry-After"] == "1"


# --- the decision line and the truncated counter ---------------------------------------------------------------------


async def test_the_decision_line_has_the_length_not_the_text(client, capsys):
    configure_logging(False)
    await client.post("/classify", json={"input": "hello world"})
    (decision,) = events(capsys.readouterr().out, "decision")
    assert decision["input_chars"] == 11 and "prompt" not in decision
    assert "hello world" not in json.dumps(decision)


async def test_truncated_requests_are_counted(client, fake_model):
    def counted(text: str) -> float:
        line = next((x for x in text.splitlines() if x.startswith("tantive_truncated_requests_total")), "x 0")
        return float(line.split()[-1])

    before = counted((await client.get("/metrics")).text)
    fake_model.script["long"] = outputs(truncated=True)
    await client.post("/classify", json={"input": ["long", "short"]})
    assert counted((await client.get("/metrics")).text) == before + 1


# --- with the real model (slow: S1D1_TEST_MODEL_DIR) -----------------------------------------------------------------


@pytest.mark.slow
async def test_a_text_of_100000_newlines_over_http(settings_factory):
    import os

    from tantive.model import OnnxModel

    path = os.environ.get("S1D1_TEST_MODEL_DIR")
    if not path:
        pytest.skip("S1D1_TEST_MODEL_DIR not set")
    app = create_app(settings_factory(), OnnxModel(path, threads=os.cpu_count() or 1, inter_op_threads=1))
    async for c in _client(app):
        r = await c.post("/v1/systemone", json={"state": "a" + "\n" * 99_998 + "b", "questions": QUESTION})
    assert r.status_code == 200
    assert r.json()["routing"]["truncated"] is True and r.json()["usage"]["input_tokens"] - 2 < 1022
