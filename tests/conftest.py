"""Shared fixtures: a FakeModel that answers from a script, and an `outputs()` helper."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import pytest

from tantive.model import Encoded, ModelInfo, Outputs
from tantive.tasks import TASKS


def outputs(
    score: float = 0.62,
    task: str = "coding_debugging",
    tools: float = 0.2,
    tokens: int = 12,
    truncated: bool = False,
) -> Outputs:
    import math

    logits = np.zeros(len(TASKS), dtype=np.float32)
    logits[TASKS.index(task)] = 8.0
    return Outputs(
        complexity_score=score,
        task_logits=logits,
        needs_tools_probability=tools,
        prompt_tokens=tokens,
        truncated=truncated,
        complexity_output=(score - 0.5) / 0.25,
        needs_tools_logit=math.log(tools / (1 - tools)),
    )


class FakeModel:
    """`run` answers each text from `script` (default `outputs()`); `on_run` sees every batch; tokens = len // 4."""

    def __init__(self) -> None:
        self.info = ModelInfo("fake", revision="0000000", sigma=0.10, sigma_source="default", onnx_file="fake.onnx")
        self.script: dict[str, Outputs] = {}
        self.on_run: Callable[[list[str]], object] | None = None
        self.on_encode: Callable[[str], object] | None = None

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def encode(self, text: str) -> Encoded:
        if self.on_encode is not None:
            self.on_encode(text)
        n = self.count_tokens(text)
        return Encoded(text=text, ids=np.arange(n, dtype=np.int64), mask=np.ones(n, dtype=np.int64), truncated=False)

    def run_encoded(self, encoded: Sequence[Encoded]) -> list[Outputs]:
        return self.run([e.text for e in encoded])

    def run(self, texts: Sequence[str]) -> list[Outputs]:
        texts = list(texts)
        if self.on_run is not None:
            self.on_run(texts)
        return [self.script.get(t) or outputs(tokens=self.count_tokens(t)) for t in texts]


@pytest.fixture
def fake_model() -> FakeModel:
    return FakeModel()


class SlowFakeModel(FakeModel):
    """A FakeModel whose `run` sleeps `fixed_s + per_token_s * tokens` per text, to exercise the scheduler."""

    def __init__(self, fixed_s: float = 0.0, per_token_s: float = 0.0) -> None:
        super().__init__()
        self.fixed_s, self.per_token_s = fixed_s, per_token_s

    def run(self, texts: Sequence[str]) -> list[Outputs]:
        import time

        for t in texts:
            time.sleep(self.fixed_s + self.per_token_s * self.count_tokens(t))
        return super().run(texts)


@pytest.fixture
def slow_fake() -> Callable[..., SlowFakeModel]:
    return SlowFakeModel


@pytest.fixture
def settings_factory():
    """Settings from a partial environment, as the service would read them."""
    from tantive.config import Settings

    def make(**env: str) -> Settings:
        return Settings.from_env({"S1D1_THREADS": "1", **env})

    return make


async def _client(app):
    import httpx

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            c.app = app
            yield c


@pytest.fixture
async def client(fake_model, settings_factory):
    from tantive.app import create_app

    async for c in _client(create_app(settings_factory(), fake_model)):
        yield c


@pytest.fixture
async def client_with_key(fake_model, settings_factory):
    from tantive.app import create_app

    async for c in _client(create_app(settings_factory(S1D1_API_KEY="k"), fake_model)):
        yield c


@pytest.fixture
def overloaded_engine(monkeypatch):
    """Every classify() is rejected, as when the queue's expected wait exceeds the deadline."""
    from tantive.engine import Engine, Overloaded

    async def reject(self, text):
        raise Overloaded(1234.0)

    monkeypatch.setattr(Engine, "classify", reject)
