"""The in-memory cache (docs/40 § 6): model outputs by a digest of the extracted text, first in first out with one
time to live, looked up before tokenisation and admission, identical requests in flight sharing one pass."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from prometheus_client import generate_latest

from tantive.app import create_app
from tantive.cache import Cache, cache_key, fingerprint
from tantive.engine import CostModel, Engine
from tantive.longprompt import LongPromptPolicy
from tantive.model import Outputs
from tantive.telemetry import configure_logging
from tests.conftest import _client, outputs

QUESTION = {"tier": {"type": "choice", "criteria": {"LOW": "", "HIGH": ""}}}


def series(name: str, **labels: str) -> float:
    """A sample of the process-wide metrics, 0 when absent."""
    total = 0.0
    for line in generate_latest().decode().splitlines():
        if line.startswith(name + "{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            total += float(line.rsplit(" ", 1)[1])
    return total


def cache_series() -> dict[str, float]:
    return {
        "hits_stored": series("tantive_cache_hits_total", source="stored"),
        "hits_in_flight": series("tantive_cache_hits_total", source="in_flight"),
        "misses": series("tantive_cache_misses_total"),
        "capacity": series("tantive_cache_evictions_total", reason="capacity"),
        "expired": series("tantive_cache_evictions_total", reason="expired"),
    }


def events(out: str, name: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if f'"event": "{name}"' in line]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


KEY = [bytes([i]) * 32 for i in range(8)]


# --- the store -------------------------------------------------------------------------------------------------------


def test_an_entry_lives_its_ttl_and_no_longer():
    clock = Clock()
    cache = Cache(ttl_s=300, max_entries=10, clock=clock)
    before = cache_series()
    cache.put(KEY[0], outputs())
    clock.now += 299
    assert cache.get(KEY[0]) is not None
    clock.now += 1
    assert cache.get(KEY[0]) is None and len(cache) == 0
    assert cache_series()["expired"] == before["expired"] + 1


def test_an_access_does_not_extend_an_entry():
    clock = Clock()
    cache = Cache(ttl_s=10, max_entries=10, clock=clock)
    cache.put(KEY[0], outputs())
    for _ in range(9):
        clock.now += 1
        assert cache.get(KEY[0]) is not None
    clock.now += 1
    assert cache.get(KEY[0]) is None


def test_the_oldest_entry_leaves_first_at_capacity():
    cache = Cache(ttl_s=300, max_entries=3, clock=Clock())
    before = cache_series()
    for k in KEY[:3]:
        cache.put(k, outputs())
    cache.get(KEY[0])  # read, not refreshed: still the oldest
    cache.put(KEY[3], outputs())
    assert cache.get(KEY[0]) is None and all(cache.get(k) is not None for k in KEY[1:4])
    assert cache_series()["capacity"] == before["capacity"] + 1


def test_an_insert_purges_the_expired_head():
    clock = Clock()
    cache = Cache(ttl_s=10, max_entries=100, clock=clock)
    before = cache_series()
    cache.put(KEY[0], outputs())
    cache.put(KEY[1], outputs())
    clock.now += 5
    cache.put(KEY[2], outputs())
    clock.now += 6  # the first two are expired, the third is not
    cache.put(KEY[3], outputs())
    assert len(cache) == 2
    assert cache_series()["expired"] == before["expired"] + 2


def test_the_entries_gauge_follows():
    cache = Cache(ttl_s=10, max_entries=2, clock=Clock())
    for k in KEY[:3]:
        cache.put(k, outputs())
    assert series("tantive_cache_entries") == 2


# --- the key ---------------------------------------------------------------------------------------------------------


def test_the_key_is_32_bytes_of_the_text_and_the_settings(fake_model):
    head_tail = fingerprint(fake_model.info, LongPromptPolicy("head_tail"))
    assert len(cache_key(head_tail, "hello")) == 32
    assert cache_key(head_tail, "hello") == cache_key(head_tail, "hello")
    assert cache_key(head_tail, "hello") != cache_key(head_tail, "hello ")  # no normalisation
    others = [
        fingerprint(fake_model.info, LongPromptPolicy("head")),
        fingerprint(fake_model.info, LongPromptPolicy("head_tail", max_input_tokens=512)),
        fingerprint(replace(fake_model.info, revision="1111111"), LongPromptPolicy("head_tail")),
        fingerprint(replace(fake_model.info, onnx_file="other.onnx"), LongPromptPolicy("head_tail")),
    ]
    assert len({head_tail, *others}) == 5
    chunks = fingerprint(fake_model.info, LongPromptPolicy("chunks"))
    assert chunks != fingerprint(fake_model.info, LongPromptPolicy("chunks", score="first"))
    # the chunks settings count only under chunks
    assert head_tail == fingerprint(fake_model.info, LongPromptPolicy("head_tail", score="first", chunk_max=3))


# --- through the engine ----------------------------------------------------------------------------------------------


async def cached_engine(model, **kw) -> Engine:
    e = Engine(model, 10_000, 32, cost=CostModel(1.0, 0.0), cache=Cache(ttl_s=300, max_entries=100), **kw)
    await e.start()
    return e


async def test_the_second_identical_text_is_a_hit_and_one_pass(fake_model):
    runs = []
    fake_model.on_run = runs.append
    e = await cached_engine(fake_model)
    first = await e.classify_cached("hello")
    second = await e.classify_cached("hello")
    await e.close()
    assert (first[2], second[2]) == ("miss", "hit")
    assert second[0] is first[0] and len(runs) == 1


async def test_identical_requests_in_flight_share_one_pass(slow_fake):
    model = slow_fake(fixed_s=0.2)
    runs = []
    model.on_run = runs.append
    e = await cached_engine(model)
    before = cache_series()
    results = await asyncio.gather(*(e.classify_cached("same text") for _ in range(5)))
    await e.close()
    assert len(runs) == 1 and len({id(r[0]) for r in results}) == 1
    assert sorted(r[2] for r in results) == ["miss", "shared", "shared", "shared", "shared"]
    after = cache_series()
    assert after["hits_in_flight"] - before["hits_in_flight"] == 4
    assert after["misses"] - before["misses"] == 1


async def test_a_failed_pass_fails_every_waiter_and_caches_nothing(slow_fake):
    model = slow_fake(fixed_s=0.1)
    calls = []

    def boom(texts):
        calls.append(texts)
        if len(calls) == 1:
            raise RuntimeError("model crashed")

    model.on_run = boom
    e = await cached_engine(model)
    results = await asyncio.gather(*(e.classify_cached("x") for _ in range(3)), return_exceptions=True)
    assert all(isinstance(r, RuntimeError) for r in results)
    assert len(e.cache) == 0
    assert (await e.classify_cached("x"))[2] == "miss"  # the next one computes again
    await e.close()


async def test_the_first_client_leaving_does_not_stop_the_others(slow_fake):
    model = slow_fake(fixed_s=0.2)
    e = await cached_engine(model)
    first = asyncio.create_task(e.classify_cached("x"))
    await asyncio.sleep(0.05)  # admitted, running
    second = asyncio.create_task(e.classify_cached("x"))
    await asyncio.sleep(0.01)
    first.cancel()
    out, _, status = await second
    await e.close()
    assert status == "shared" and isinstance(out, Outputs)
    assert len(e.cache) == 1 and e.pending_cost_ms == 0 and e.queued == 0


async def test_a_waiter_leaving_does_not_stop_the_pass(slow_fake):
    model = slow_fake(fixed_s=0.2)
    e = await cached_engine(model)
    first = asyncio.create_task(e.classify_cached("x"))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(e.classify_cached("x"))
    await asyncio.sleep(0.01)
    second.cancel()
    out, _, status = await first
    await e.close()
    assert status == "miss" and isinstance(out, Outputs) and len(e.cache) == 1


async def test_a_shed_request_leaves_no_pass_behind(slow_fake):
    """A request refused at admission creates no shared pass: the next identical one tries again."""
    from tantive.engine import Overloaded

    e = await cached_engine(slow_fake(fixed_s=0.0))
    e.max_queue = 0  # every admission refused
    with pytest.raises(Overloaded):
        await e.classify_cached("x")
    e.max_queue = 32
    assert (await e.classify_cached("x"))[2] == "miss"
    await e.close()


# --- over HTTP -------------------------------------------------------------------------------------------------------


def cached_settings(settings_factory, **env):
    return settings_factory(S1D1_CACHE_TTL_S="300", **env)


async def test_a_hit_answers_the_same_body(fake_model, settings_factory, capsys):
    configure_logging(False)
    runs = []
    body = {"state": "how do I fix this bug?", "questions": QUESTION}
    async for c in _client(create_app(cached_settings(settings_factory), fake_model)):
        fake_model.on_run = runs.append  # after the warm-up
        first = (await c.post("/v1/systemone", json=body)).json()
        second = (await c.post("/v1/systemone", json=body)).json()
    first["routing"].pop("latency_ms"), second["routing"].pop("latency_ms")
    assert first == second and len(runs) == 1
    assert [d["cache"] for d in events(capsys.readouterr().out, "decision")] == ["miss", "hit"]


async def test_a_hit_answers_while_the_queue_is_full(fake_model, settings_factory):
    body = {"state": "cached", "questions": QUESTION}
    async for c in _client(create_app(cached_settings(settings_factory), fake_model)):
        assert (await c.post("/v1/systemone", json=body)).status_code == 200
        c.app.state.engine.max_queue = 0  # queued >= max_queue: every admission is refused, as with a full queue
        assert (await c.post("/v1/systemone", json={**body, "state": "new"})).status_code == 503
        assert (await c.post("/v1/systemone", json=body)).status_code == 200


async def test_n_identical_requests_over_http(slow_fake, settings_factory, monkeypatch):
    monkeypatch.setattr(Engine, "warmup", lambda self: CostModel(1.0, 0.0))
    model = slow_fake(fixed_s=0.2)
    runs = []
    model.on_run = runs.append
    before = cache_series()
    async for c in _client(create_app(cached_settings(settings_factory), model)):
        body = {"state": "x", "questions": QUESTION}
        rs = await asyncio.gather(*(c.post("/v1/systemone", json=body) for _ in range(6)))
    assert [r.status_code for r in rs] == [200] * 6 and len(runs) == 1
    assert cache_series()["hits_in_flight"] - before["hits_in_flight"] == 5


async def test_the_tier_is_computed_again_on_a_hit(fake_model, settings_factory):
    from tantive.questions import Catalogue
    from tantive.tiers import native_set

    fake_model.script["x"] = outputs(score=0.62)
    async for c in _client(create_app(cached_settings(settings_factory), fake_model)):
        low_high = (await c.post("/v1/systemone", json={"state": "x", "questions": QUESTION})).json()
        options = {"tier": {"type": "choice", "criteria": {"SIMPLE": "", "MEDIUM": "", "COMPLEX": ""}}}
        three = (await c.post("/v1/systemone", json={"state": "x", "questions": options})).json()
        c.app.state.native = native_set("SMALL=0.5,BIG=1.0")  # another S1D1_TIERS, same cached outputs
        c.app.state.catalogue = Catalogue(c.app.state.native, c.app.state.info.sigma)
        r = await c.post("/classify", json={"input": "x"})
    assert low_high["answers"]["tier"]["choice"] in ("LOW", "HIGH")
    assert three["answers"]["tier"]["choice"] in ("SIMPLE", "MEDIUM", "COMPLEX")
    assert low_high["routing"]["complexity_score"] == three["routing"]["complexity_score"] == 0.62
    assert r.json()["data"][0]["label"] == "BIG"


async def test_neither_the_text_nor_the_digest_leaves_the_process(fake_model, settings_factory, capsys):
    configure_logging(False)
    text = "my secret question about tax"
    app = create_app(cached_settings(settings_factory), fake_model)
    async for c in _client(app):
        for _ in range(2):
            await c.post("/classify", json={"input": text})
        info = (await c.get("/info")).text
        metrics = (await c.get("/metrics")).text
    out = capsys.readouterr().out
    cache = app.state.engine.cache
    (key,) = list(cache._entries)
    assert isinstance(key, bytes) and len(key) == 32
    assert all(isinstance(value, Outputs) for _, value in cache._entries.values())
    for where in (out, info, metrics):
        assert text not in where and key.hex() not in where and key.hex()[:16] not in where
    assert json.loads(info)["cache"] == {"ttl_s": 300, "max_entries": 10_000}


async def test_log_prompts_keeps_its_effect_on_a_hit(fake_model, settings_factory, capsys):
    configure_logging(True)
    try:
        async for c in _client(create_app(cached_settings(settings_factory), fake_model)):
            for _ in range(2):
                await c.post("/classify", json={"input": "visible on purpose"})
    finally:
        configure_logging(False)
    assert [d.get("prompt") for d in events(capsys.readouterr().out, "decision")] == ["visible on purpose"] * 2


async def test_a_hit_or_a_shared_answer_is_not_captured(slow_fake, settings_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "warmup", lambda self: CostModel(1.0, 0.0))
    model = slow_fake(fixed_s=0.1)
    app = create_app(cached_settings(settings_factory, S1D1_CAPTURE_DIR=str(tmp_path)), model)
    offered = []
    async for c in _client(app):
        monkeypatch.setattr(app.state.capture, "offer", lambda prompt, *a: offered.append(prompt))
        await asyncio.gather(*(c.post("/classify", json={"input": "same"}) for _ in range(3)))  # 1 miss, 2 shared
        await c.post("/classify", json={"input": "same"})  # a hit
        await c.post("/classify", json={"input": "other"})  # a miss
    assert offered == ["same", "other"]


async def test_cache_off_creates_nothing(client, capsys):
    configure_logging(False)
    before = cache_series()
    for _ in range(2):
        await client.post("/classify", json={"input": "hello"})
    assert client.app.state.engine.cache is None
    assert cache_series() == before
    assert all("cache" not in d for d in events(capsys.readouterr().out, "decision"))
    assert (await client.get("/info")).json()["cache"] == {"ttl_s": 0, "max_entries": 10_000}


async def test_the_ready_line_shows_the_cache(fake_model, settings_factory, capsys):
    configure_logging(False)
    async for _ in _client(create_app(cached_settings(settings_factory, S1D1_CACHE_MAX_ENTRIES="50"), fake_model)):
        pass
    (ready,) = events(capsys.readouterr().out, "ready")
    assert (ready["cache_ttl_s"], ready["cache_max_entries"]) == (300, 50)


def test_memory_of_10000_entries():
    """About 1 KiB per entry (docs/40 § 6): measured, the figure goes in the PR."""
    import hashlib
    import tracemalloc

    import numpy as np

    from tantive.tasks import TASKS

    cache = Cache(ttl_s=300, max_entries=10_000)
    rng = np.random.default_rng(0)
    tracemalloc.start()
    start = tracemalloc.take_snapshot()
    for i in range(10_000):
        logits = rng.normal(size=len(TASKS)).astype(np.float32)
        out = Outputs(0.5, logits, 0.2, 300, False, complexity_output=0.1, needs_tools_logit=-1.0)
        cache.put(hashlib.sha256(str(i).encode()).digest(), out)
    used = sum(s.size_diff for s in tracemalloc.take_snapshot().compare_to(start, "filename"))
    tracemalloc.stop()
    print(f"10000 entries: {used / 1024 / 1024:.2f} MiB, {used / 10_000:.0f} bytes each")
    assert len(cache) == 10_000 and used < 20 * 1024 * 1024


@pytest.mark.slow
async def test_a_hit_is_the_body_a_new_pass_gives(settings_factory, monkeypatch):
    """The pass is deterministic on CPU for one text alone: the cache changes nothing but latency_ms (docs/40 § 6)."""
    import os

    path = os.environ.get("S1D1_TEST_MODEL_DIR")
    if not path:
        pytest.skip("S1D1_TEST_MODEL_DIR not set")
    from tantive.model import OnnxModel
    from tests.fixtures import corpus

    model = OnnxModel(path, threads=os.cpu_count() or 1, inter_op_threads=1)
    monkeypatch.setattr(Engine, "warmup", lambda self: CostModel(1.0, 0.0))
    for lang in ("english", "chinese"):
        body = {"state": corpus.text(lang, 20_000), "questions": QUESTION}
        bodies = []
        for ttl in ("0", "300"):
            async for c in _client(create_app(settings_factory(S1D1_CACHE_TTL_S=ttl), model)):
                for _ in range(2):
                    r = (await c.post("/v1/systemone", json=body)).json()
                    r["routing"].pop("latency_ms")
                    bodies.append(json.dumps(r, sort_keys=True))
        assert len(set(bodies)) == 1, lang  # two passes without the cache, one pass and a hit with it
