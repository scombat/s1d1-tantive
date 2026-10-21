"""Long-prompt policies: how a text becomes pieces and how the pieces' outputs become one (docs/24 of the research
repo: head_tail by default, chunks as an option). The reference is `pieces_of` and `merge` of
pipeline/11_long_prompts.py."""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

from tantive.config import ConfigError, Settings
from tantive.engine import CostModel, Engine
from tantive.longprompt import LongPromptPolicy, merge, split
from tantive.model import Encoded, Outputs
from tantive.tasks import TASKS

BOS, EOS = 900_001, 900_002
WINDOW = 1024
USEFUL = WINDOW - 2


def encoded(n_ids: int, text: str = "t") -> Encoded:
    """CLS + n_ids useful ids (0 .. n_ids - 1) + SEP, as the model's encode() returns them (untruncated)."""
    ids = np.array([BOS, *range(n_ids), EOS], dtype=np.int64)
    return Encoded(text=text, ids=ids, mask=np.ones(len(ids), dtype=np.int64), truncated=False)


def body(piece: Encoded) -> list[int]:
    assert piece.ids[0] == BOS and piece.ids[-1] == EOS
    return piece.ids[1:-1].tolist()


def policy(name: str = "head_tail", **kw) -> LongPromptPolicy:
    return LongPromptPolicy(name, window=WINDOW, **kw)


def row(score: float, task: str = "coding_debugging", logit: float = 8.0, tools: float = 0.0) -> Outputs:
    logits = np.zeros(len(TASKS), dtype=np.float32)
    logits[TASKS.index(task)] = logit
    return Outputs(
        complexity_score=0.5 + 0.25 * score,
        task_logits=logits,
        needs_tools_probability=0.5,
        prompt_tokens=10,
        truncated=False,
        complexity_output=score,
        needs_tools_logit=tools,
    )


@pytest.mark.parametrize("name", ["head", "tail", "head_tail", "chunks"])
def test_window_exact_is_one_piece(name):
    for n in (5, USEFUL):  # 1 022 useful ids + CLS + SEP is exactly the window
        pieces, truncated = split(encoded(n), policy(name))
        assert len(pieces) == 1 and truncated is False
        assert pieces[0].ids.tolist() == encoded(n).ids.tolist()
    pieces, truncated = split(encoded(USEFUL + 1), policy(name))
    assert max(len(p.ids) for p in pieces) <= WINDOW
    assert truncated is (name != "chunks")  # chunks covers the extra id with a second piece


def test_head_is_todays_behaviour():
    pieces, truncated = split(encoded(3000), policy("head"))
    assert len(pieces) == 1 and truncated is True
    assert body(pieces[0]) == list(range(USEFUL))
    assert len(pieces[0].ids) == WINDOW


def test_tail_and_head_tail_pieces():
    (tail,), cut = split(encoded(3000), policy("tail"))
    assert cut is True and body(tail) == list(range(3000 - USEFUL, 3000))
    (ht,), cut = split(encoded(3000), policy("head_tail"))
    assert cut is True
    assert body(ht) == list(range(511)) + list(range(3000 - 511, 3000))
    assert len(ht.ids) == 1024


def test_chunks_overlap():
    (a, b, c), cut = split(encoded(2500), policy("chunks"))  # size 1 022, stride 958
    assert cut is False
    assert body(a) == list(range(0, 1022))
    assert body(b) == list(range(958, 958 + 1022))
    assert body(c) == list(range(1916, 2500))
    assert set(body(a)) & set(body(b)) == set(range(958, 1022))  # the 64 shared ids


def test_chunk_max_drops_remainder():
    pieces, truncated = split(encoded(20_000), policy("chunks", chunk_max=3))
    assert len(pieces) == 3 and truncated is True
    assert body(pieces[-1])[-1] == 2 * 958 + 1022 - 1


def test_chunks_exactly_covering_is_not_truncated():
    n = 958 + 1022  # two pieces end exactly at the last id
    pieces, truncated = split(encoded(n), policy("chunks", chunk_max=2))
    assert len(pieces) == 2 and truncated is False


def test_chunk_tokens_smaller_than_window():
    pieces, _ = split(encoded(1500), policy("chunks", chunk_tokens=512, overlap=64))
    assert all(len(p.ids) <= 512 for p in pieces)
    assert body(pieces[1])[0] == 510 - 64


def test_pieces_are_token_aligned():
    """Pieces are cut on ids, never on text: every id is an id of the full encoding, in order, specials kept."""
    full = encoded(2500)
    for name in ("head", "tail", "head_tail", "chunks"):
        pieces, _ = split(full, policy(name))
        for p in pieces:
            ids = body(p)
            assert set(ids) <= set(range(2500))
            assert p.mask.tolist() == [1] * len(p.ids)
            assert p.text == full.text


def reference():
    """`pieces_of` and `merge` of the research script, lifted from its source (the script itself needs pyarrow, torch
    and the Router at import time, which this service does not have)."""
    import ast

    path = "/Users/steve/work/s1d1-model-router/pipeline/11_long_prompts.py"
    if not os.path.isfile(path):
        pytest.skip("research repo not available")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("pieces_of", "merge")]
    assert {n.name for n in wanted} == {"pieces_of", "merge"}
    namespace = {"np": np, "MAX_LENGTH": 1024, "USEFUL": 1022, "N_TASKS": len(TASKS)}
    exec(compile(ast.Module(wanted, []), path, "exec"), namespace)  # noqa: S102
    return SimpleNamespace(pieces_of=namespace["pieces_of"], merge=namespace["merge"])


@pytest.mark.parametrize("n", [1023, 1500, 2043, 2044, 2980, 3000, 9000, 20_000])
@pytest.mark.parametrize("name", ["head", "tail", "head_tail", "chunks"])
def test_pieces_match_research_script(name, n):
    ref = reference()
    ids = list(range(n))
    expected = ref.pieces_of(ids, name)
    got, _ = split(encoded(n), policy(name))
    assert [body(p) for p in got] == expected


def hand_computed(n: int, name: str) -> list[list[int]]:
    """The same cases computed by hand (used when the research script cannot be imported)."""
    ids = list(range(n))
    if n <= USEFUL:
        return [ids]
    if name == "head":
        return [ids[:USEFUL]]
    if name == "tail":
        return [ids[-USEFUL:]]
    if name == "head_tail":
        return [ids[:511] + ids[-511:]]
    out, start = [], 0
    while True:
        out.append(ids[start : start + 1022])
        if start + 1022 >= n or len(out) == 8:
            return out
        start += 958


@pytest.mark.parametrize("n", [1023, 2043, 2044, 3000, 20_000])
@pytest.mark.parametrize("name", ["head", "tail", "head_tail", "chunks"])
def test_pieces_match_hand_computed(name, n):
    got, _ = split(encoded(n), policy(name))
    assert [body(p) for p in got] == hand_computed(n, name)


def test_merge_score_strategies():
    outs = [row(-1.0), row(0.0), row(3.0)]
    lengths = [100, 100, 800]
    center, scale = 0.5, 0.25

    def merged(score):
        return merge(outs, lengths, score, "sum_logits").complexity_output

    assert merged("max") == pytest.approx(3.0)
    assert merged("mean") == pytest.approx(2 / 3)
    assert merged("median") == pytest.approx(0.0)
    assert merged("first") == pytest.approx(-1.0)
    assert merged("length_weighted") == pytest.approx((-100 + 0 + 2400) / 1000)
    assert merge(outs, lengths, "max", "sum_logits", center, scale).complexity_score == 1.0  # 0.5 + 0.75 clipped


def test_merge_task_vote_and_sum():
    outs = [row(0, "coding_debugging", 3.0), row(0, "coding_debugging", 2.0), row(0, "translation", 9.0)]
    lengths = [10, 10, 10]
    assert merge(outs, lengths, "max", "sum_logits").task == "translation"  # 9 beats 3 + 2
    assert merge(outs, lengths, "max", "vote").task == "coding_debugging"  # two votes to one
    tie = [row(0, "coding_debugging", 3.0), row(0, "translation", 5.0)]
    assert merge(tie, [10, 10], "max", "vote").task == "translation"  # tie: the larger logit wins


def test_merge_tools_max():
    outs = [row(0, tools=-2.0), row(0, tools=1.0), row(0, tools=-0.5)]
    m = merge(outs, [10, 10, 10], "max", "sum_logits")
    assert m.needs_tools_logit == pytest.approx(1.0)
    assert m.needs_tools_probability == pytest.approx(0.731, abs=1e-3)


def test_merge_counts_pieces_and_tokens():
    m = merge([row(0), row(0)], [1024, 300], "max", "sum_logits", truncated=True)
    assert (m.pieces, m.prompt_tokens, m.truncated) == (2, 1324, True)
    single = row(0)
    assert merge([single], [10], "max", "sum_logits") is single  # one piece: the outputs untouched


def test_merge_matches_research_script():
    ref = reference()
    rng = np.random.default_rng(0)
    rows = rng.normal(size=(4, 21)).astype(np.float32)
    outs = [
        Outputs(0.5, r[1:20].copy(), 0.5, 0, False, complexity_output=float(r[0]), needs_tools_logit=float(r[20]))
        for r in rows
    ]
    for score in ("max", "mean", "median", "first", "length_weighted"):
        for task in ("sum_logits", "vote"):
            want = ref.merge(list(rows), [1024, 1024, 1024, 300], score, task)
            got = merge(outs, [1024, 1024, 1024, 300], score, task)
            assert got.complexity_output == pytest.approx(float(want[0]), abs=1e-6)
            if task == "sum_logits":
                assert np.allclose(got.task_logits, want[1:20], atol=1e-5)
            assert got.task == TASKS[int(np.argmax(want[1:20]))]
            assert got.needs_tools_logit == pytest.approx(float(want[20]), abs=1e-6)


class CountingModel:
    """Counts the batches given to run_encoded and gives each piece a distinct score."""

    def __init__(self, n_ids: int) -> None:
        from tantive.model import ModelInfo

        self.info = ModelInfo("fake", "0", 0.1, "default", "fake.onnx")
        self.n_ids = n_ids
        self.batches: list[int] = []

    def count_tokens(self, text):
        return min(self.n_ids + 2, WINDOW)

    def encode(self, text):
        return encoded(self.n_ids, text)

    def run_encoded(self, pieces):
        self.batches.append(len(pieces))
        from dataclasses import replace

        return [replace(row(float(i)), truncated=p.truncated) for i, p in enumerate(pieces)]

    def run(self, texts):
        raise AssertionError("not used")


async def test_pieces_run_in_one_call_and_merge():
    model = CountingModel(2500)
    e = Engine(model, 10_000, 32, cost=CostModel(1.0, 0.01), policy=policy("chunks"))
    await e.start()
    out, _ = await e.classify("long")
    await e.close()
    assert model.batches == [3]
    assert out.pieces == 3 and out.complexity_output == 2.0  # max of 0, 1, 2
    assert out.prompt_tokens == 1024 + 1024 + (2500 - 1916 + 2)


async def test_default_policy_is_one_piece_for_long_text():
    model = CountingModel(5000)
    e = Engine(model, 10_000, 32, cost=CostModel(1.0, 0.01))
    await e.start()
    out, _ = await e.classify("long")
    await e.close()
    assert model.batches == [1] and out.pieces == 1 and out.truncated is True


async def test_cost_counts_pieces():
    from tantive.engine import Overloaded

    cost = CostModel(fixed_ms=10.0, per_token_ms=0.1)
    # one piece of 1024 tokens: 10 + 102.4 = 112 ms, accepted; three pieces: 3 * 10 + 0.1 * 2 * 1024 + ... > 250
    model = CountingModel(2500)
    e = Engine(model, deadline_ms=250, max_queue=32, cost=cost, policy=policy("head_tail"))
    await e.start()
    await e.classify("x")
    await e.close()
    e = Engine(model, deadline_ms=250, max_queue=32, cost=cost, policy=policy("chunks"))
    await e.start()
    with pytest.raises(Overloaded) as ex:
        await e.classify("x")
    await e.close()
    assert ex.value.expected_wait_ms == pytest.approx(3 * 10.0 + 0.1 * (1024 + 1024 + 586))


def test_cost_model_predicts_pieces():
    assert CostModel(2.0, 0.1).predict_pieces([100, 50]) == pytest.approx(2 * 2.0 + 0.1 * 150)


def test_settings_defaults_follow_the_measurement():
    s = Settings.from_env({})
    assert (s.long_prompts, s.chunk_tokens, s.chunk_overlap, s.chunk_max) == ("head_tail", 1024, 64, 8)
    assert (s.chunk_score, s.chunk_task) == ("max", "sum_logits")


@pytest.mark.parametrize(
    "env",
    [
        {"S1D1_LONG_PROMPTS": "middle"},
        {"S1D1_CHUNK_TOKENS": "2048"},
        {"S1D1_CHUNK_TOKENS": "100"},
        {"S1D1_CHUNK_OVERLAP": "1022"},
        {"S1D1_CHUNK_OVERLAP": "-1"},
        {"S1D1_CHUNK_MAX": "0"},
        {"S1D1_CHUNK_MAX": "33"},
        {"S1D1_CHUNK_SCORE": "sum"},
        {"S1D1_CHUNK_TASK": "maj"},
    ],
)
def test_settings_validation(env):
    (name,) = env
    with pytest.raises(ConfigError, match=name):
        Settings.from_env(env)


def test_settings_accept_every_listed_value():
    for p in ("head", "tail", "head_tail", "chunks"):
        assert Settings.from_env({"S1D1_LONG_PROMPTS": p}).long_prompts == p
    for sc in ("max", "mean", "median", "first", "length_weighted"):
        assert Settings.from_env({"S1D1_CHUNK_SCORE": sc}).chunk_score == sc
    s = Settings.from_env({"S1D1_CHUNK_TOKENS": "512", "S1D1_CHUNK_OVERLAP": "32", "S1D1_CHUNK_MAX": "4"})
    assert (s.chunk_tokens, s.chunk_overlap, s.chunk_max) == (512, 32, 4)


def test_policy_from_settings():
    s = Settings.from_env({"S1D1_LONG_PROMPTS": "chunks", "S1D1_CHUNK_SCORE": "mean"})
    p = LongPromptPolicy.from_settings(s, window=1024)
    assert (p.name, p.score, p.task, p.chunk_max) == ("chunks", "mean", "sum_logits", 8)
