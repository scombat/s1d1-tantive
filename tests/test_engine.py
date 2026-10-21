import asyncio
import time

import pytest

from tantive.engine import CostModel, Engine, Overloaded


@pytest.fixture
async def engine(fake_model):
    e = Engine(fake_model, deadline_ms=800, max_queue=32)
    await e.start()
    yield e
    await e.close()


def test_cost_model_predicts_linear():
    assert CostModel(fixed_ms=2.0, per_token_ms=0.1).predict(100) == pytest.approx(12.0)


async def test_classify_returns_outputs_and_latency(engine):
    out, ms = await engine.classify("hello")
    assert out.complexity_score == 0.62
    assert ms >= 0
    assert engine.ready is True
    assert engine.queued == 0


async def test_not_ready_before_start(fake_model):
    e = Engine(fake_model, 800, 32)
    assert e.ready is False
    assert e.cost is None


def test_warmup_fits_cost_model(slow_fake):
    e = Engine(slow_fake(per_token_s=0.0001, fixed_s=0.002), 800, 32)
    c = e.warmup()
    assert 0.05 < c.per_token_ms < 0.2
    assert 1 < c.fixed_ms < 10  # time.sleep overshoots by a few ms on a laptop


async def test_overloaded_rejects_fast(slow_fake):
    e = Engine(slow_fake(per_token_s=0.0, fixed_s=0.2), deadline_ms=250, max_queue=32)
    await e.start()
    t1 = asyncio.create_task(e.classify("a"))
    await asyncio.sleep(0.01)
    t0 = time.perf_counter()
    with pytest.raises(Overloaded) as ex:
        await e.classify("b")
    assert time.perf_counter() - t0 < 0.05
    assert ex.value.expected_wait_ms > 250
    await t1
    await e.close()


async def test_queue_cap(slow_fake):
    e = Engine(slow_fake(fixed_s=0.05), deadline_ms=10_000, max_queue=2)
    await e.start()
    tasks = [asyncio.create_task(e.classify(str(i))) for i in range(3)]
    await asyncio.sleep(0.005)
    with pytest.raises(Overloaded):
        await e.classify("x")
    await asyncio.gather(*tasks)
    assert e.queued == 0
    await e.close()


async def test_single_thread_serialises(slow_fake):
    e = Engine(slow_fake(fixed_s=0.05), 10_000, 32)
    await e.start()
    t0 = time.perf_counter()
    await asyncio.gather(e.classify("a"), e.classify("b"))
    assert time.perf_counter() - t0 >= 0.1
    await e.close()


async def test_pending_cost_returns_to_zero_after_failure(fake_model):
    def boom(texts):
        raise RuntimeError("model crashed")

    fake_model.on_run = boom
    e = Engine(fake_model, 800, 32, cost=CostModel(1.0, 0.01))  # a preset cost model skips the warm-up
    await e.start()
    with pytest.raises(RuntimeError):
        await e.classify("x")
    assert e.pending_cost_ms == 0
    assert e.queued == 0
    await e.close()


async def test_classify_tokenises_once_and_off_the_loop(engine, fake_model):
    import threading

    encodes, runs, threads = [], [], []
    fake_model.on_encode = lambda text: (encodes.append(text), threads.append(threading.current_thread().name))
    fake_model.on_run = runs.append
    await engine.classify("hello world")
    assert encodes == ["hello world"]  # one tokenisation per request...
    assert runs == [["hello world"]]  # ...and one pass
    assert threads[0].startswith("s1d1-tokenize")  # off the event loop, and not behind the inference queue
