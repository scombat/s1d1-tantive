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
from tantive.longprompt import POLICIES, LongPromptPolicy, encode, merge, split
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


# --- the cut before tokenisation (docs/40 § 5): only what the policy can keep is tokenised ---------------------------

SPANS = {  # characters tokenised at most, at 8 characters per kept token, with the window as the only limit
    "head": 8 * USEFUL,
    "tail": 8 * USEFUL,
    "head_tail": 2 * 8 * (USEFUL // 2),
    "chunks": 8 * 8 * WINDOW,  # chunk_max pieces of chunk_tokens, overlaps and CLS/SEP included
}


class CharModel:
    """One token per character, CLS and SEP around: a cut on characters is a cut on tokens. `per_token` characters
    make one token instead, for texts that need more characters per token than the margin assumes."""

    def __init__(self, per_token: int = 1) -> None:
        from tantive.model import ModelInfo

        self.info = ModelInfo("fake", "0", 0.1, "default", "fake.onnx")
        self.per_token = per_token
        self.seen: list[str] = []

    def encode(self, text: str) -> Encoded:
        self.seen.append(text)
        if self.per_token == 1:
            body = [ord(c) for c in text]
        else:
            body = [ord(text[i]) for i in range(0, len(text) - self.per_token + 1, self.per_token)]
        ids = np.array([BOS, *body, EOS], dtype=np.int64)
        return Encoded(text=text, ids=ids, mask=np.ones(len(ids), dtype=np.int64), truncated=False)


def piece_ids(pieces: list[Encoded]) -> list[list[int]]:
    return [p.ids.tolist() for p in pieces]


def varied(chars: int) -> str:
    """A text whose every character differs from its neighbours, so a misplaced cut shows."""
    return "".join(chr(0x4E00 + (i * 7919) % 20_000) for i in range(chars))


@pytest.mark.parametrize("name", POLICIES)
def test_text_within_its_cut_is_encoded_whole(name):
    model = CharModel()
    text = varied(SPANS[name])  # exactly the cut: nothing to remove
    e = encode(model, text, policy(name))
    assert model.seen == [text]
    assert e.truncated is False and e.ids.tolist() == model.encode(text).ids.tolist()


@pytest.mark.parametrize("name", POLICIES)
def test_cut_text_gives_the_pieces_of_the_whole_text(name):
    for chars in (SPANS[name] + 1, 3 * SPANS[name]):
        text, model = varied(chars), CharModel()
        cut, _ = split(encode(model, text, policy(name)), policy(name))
        whole, truncated = split(model.encode(text), policy(name))
        assert piece_ids(cut) == piece_ids(whole)
        assert all(p.truncated == truncated for p in cut)


def test_head_tail_tokenises_both_ends_apart():
    text, model = varied(100_000), CharModel()
    e = encode(model, text, policy("head_tail"))
    assert model.seen == [text[: 8 * 511], text[-8 * 511 :]]  # never joined: no merge across the gap
    assert e.ids.tolist() == [BOS, *(ord(c) for c in text[:511]), *(ord(c) for c in text[-511:]), EOS]
    assert e.truncated is True and e.text == text


@pytest.mark.parametrize("name", POLICIES)
async def test_a_2_mib_text_never_reaches_the_tokenizer_whole(fake_model, name):
    lengths = []
    fake_model.on_encode = lambda text: lengths.append(len(text))
    e = Engine(fake_model, 10**9, 32, cost=CostModel(1.0, 0.0), policy=policy(name))
    await e.start()
    await e.classify("word " * (2 * 1024 * 1024 // 5))
    await e.close()
    assert lengths and sum(lengths) <= SPANS[name]
    if name == "head_tail":
        assert lengths == [8 * 511, 8 * 511]


@pytest.mark.parametrize("name", POLICIES)
def test_fewer_tokens_than_the_margin_assumes_is_accepted(name):
    model = CharModel(per_token=30)  # a run of newlines: about 30 characters per token
    text = varied(100_000)
    pieces, truncated = split(encode(model, text, policy(name)), policy(name))
    kept = sum(len(p.ids) - 2 for p in pieces)
    assert truncated is True and all(p.truncated for p in pieces)
    assert kept < (USEFUL if name != "chunks" else 8 * USEFUL)
    assert max(len(p.ids) for p in pieces) <= WINDOW


async def test_tokenisation_is_timed(client):
    await client.post("/classify", json={"input": "hello"})
    assert "tantive_tokenize_seconds_bucket" in (await client.get("/metrics")).text


# --- the same with the real tokenizer (slow: S1D1_TEST_MODEL_DIR) ----------------------------------------------------


@pytest.fixture(scope="module")
def onnx_model():
    path = os.environ.get("S1D1_TEST_MODEL_DIR")
    if not path:
        pytest.skip("S1D1_TEST_MODEL_DIR not set")
    from tantive.model import OnnxModel

    return OnnxModel(path, threads=os.cpu_count() or 1, inter_op_threads=1)


def at(name: str, useful: int) -> LongPromptPolicy:
    """The policy with `useful` ids per piece (1 022, 510 or 254)."""
    return LongPromptPolicy(name, window=useful + 2, chunk_tokens=useful + 2)


@pytest.mark.slow
def test_cut_keeps_the_ids_of_the_full_encoding_on_the_corpus(onnx_model):
    from tests.fixtures import corpus

    for lang in corpus.NAMES:
        for chars in (20_000, 100_000):
            text = corpus.text(lang, chars)
            whole = onnx_model.encode(text)
            for name in POLICIES:
                for useful in (1022, 510, 254):
                    for p in (at(name, useful), limited(name, useful + 2)):  # a smaller window, or an input limit
                        cut = encode(onnx_model, text, p)
                        case = (lang, chars, name, useful, p.max_input_tokens)
                        assert piece_ids(split(cut, p)[0]) == piece_ids(split(whole, p)[0]), case
                        assert chars == 20_000 or cut.truncated is True  # 100 000 characters are beyond every cut


def random_text(rng: np.random.Generator, chars: int) -> str:
    """Words of 1 to 12 characters, one in twenty of 500 to 40 000 without a space (Latin, digits, CJK, symbols): the
    long ones straddle the kept ids and the cut, where a BPE merge could in theory differ (docs/40 § 5)."""
    alphabet = np.array(
        list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789éèàçœß=+-_/.,;:!?()[]{}#@*&%$")
        + [chr(0x4E00 + i) for i in range(300)]
    )
    words, n = [], 0
    while n < chars:
        length = int(rng.integers(500, 40_000)) if rng.random() < 0.05 else int(rng.integers(1, 13))
        words.append("".join(rng.choice(alphabet, size=length)) + (" " if rng.random() < 0.8 else "\n"))
        n += length + 1
    return "".join(words)[:chars]


@pytest.mark.slow
def test_cut_keeps_the_ids_of_the_full_encoding_on_random_long_words(onnx_model):
    rng = np.random.default_rng(40)
    for _ in range(20):
        text = random_text(rng, 80_000)
        whole = onnx_model.encode(text)
        for name in POLICIES:
            for useful in (1022, 510, 254):
                p = at(name, useful)
                assert piece_ids(split(encode(onnx_model, text, p), p)[0]) == piece_ids(split(whole, p)[0])


@pytest.mark.slow
async def test_a_text_of_newlines_is_classified_on_fewer_tokens(onnx_model):
    e = Engine(onnx_model, 10**9, 32, cost=CostModel(1.0, 0.0))
    await e.start()
    out, _ = await e.classify("a" + "\n" * 99_998 + "b")  # about 30 characters per token: the cut keeps less than U
    await e.close()
    assert out.truncated is True and out.prompt_tokens - 2 < USEFUL


@pytest.mark.slow
async def test_a_text_of_newlines_over_http(onnx_model, settings_factory):
    import httpx

    from tantive.app import create_app

    app = create_app(settings_factory(), onnx_model)
    state = "a" + "\n" * 48_998 + "b"  # 49 000 characters: under the 413 of 50 000
    question = {"tier": {"type": "choice", "criteria": {"SIMPLE": "", "COMPLEX": ""}}}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/v1/systemone", json={"state": state, "questions": question})
    assert r.status_code == 200
    assert r.json()["routing"]["truncated"] is True and r.json()["usage"]["input_tokens"] - 2 < USEFUL


@pytest.mark.slow
def test_a_2_mib_text_tokenises_in_under_5_ms(onnx_model):
    import time

    from tests.fixtures import corpus

    text, p = corpus.text("english", 2 * 1024 * 1024), policy("head_tail")
    timings = []
    for _ in range(5):
        t0 = time.perf_counter()
        encode(onnx_model, text, p)
        timings.append(time.perf_counter() - t0)
    assert min(timings) < 0.005, timings


@pytest.mark.slow
async def test_outputs_match_0_8_1_on_the_corpus(onnx_model):
    """The engine's outputs on the fixture corpus, as recorded with 0.8.1 before the cut existed: same piece ids
    (exactly), same outputs (to float noise across machines)."""
    import hashlib
    import json

    from tests.fixtures import corpus

    if onnx_model.info.onnx_file != "model_int8.onnx" or onnx_model.info.max_length != WINDOW:
        pytest.skip("the reference was recorded with small-onnx-int8")
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "reference_0_8_1.json"), encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    seen: list[list[np.ndarray]] = []

    class Spy:
        info = onnx_model.info

        def encode(self, text):
            return onnx_model.encode(text)

        def run_encoded(self, pieces):
            seen.append([p.ids for p in pieces])
            return onnx_model.run_encoded(pieces)

    for name in POLICIES:
        e = Engine(
            Spy(),
            10**9,
            32,
            cost=CostModel(1.0, 0.0),
            policy=LongPromptPolicy.from_settings(Settings.from_env({"S1D1_LONG_PROMPTS": name}), WINDOW),
        )
        await e.start()
        for key, want in cases.items():
            lang, chars, policy_name = key.split("/")
            if policy_name != name:
                continue
            seen.clear()
            out, _ = await e.classify(corpus.text(lang, int(chars)))
            (pieces,) = seen
            assert [len(p) for p in pieces] == want["piece_lengths"], key
            assert hashlib.sha256(b"".join(p.tobytes() for p in pieces)).hexdigest() == want["piece_ids_sha256"], key
            assert (out.prompt_tokens, out.truncated, out.pieces) == (
                want["prompt_tokens"],
                want["truncated"],
                want["pieces"],
            ), key
            assert out.complexity_output == pytest.approx(want["complexity_output"], abs=1e-4), key
            assert out.needs_tools_logit == pytest.approx(want["needs_tools_logit"], abs=1e-4), key
            assert np.allclose(out.task_logits, want["task_logits"], atol=1e-4), key
        await e.close()


# --- the input limit (docs/40 § 4): at most L tokens passed to the model, CLS and SEP included -----------------------


def limited(name: str, limit: int | None, **kw) -> LongPromptPolicy:
    return LongPromptPolicy(name, window=WINDOW, max_input_tokens=limit, **kw)


@pytest.mark.parametrize("limit", [128, 255, 256, 512, 1024])
def test_single_piece_policies_keep_the_limit(limit):
    u = limit - 2
    (head,), cut = split(encoded(3000), limited("head", limit))
    assert cut is True and body(head) == list(range(u))
    (tail,), cut = split(encoded(3000), limited("tail", limit))
    assert cut is True and body(tail) == list(range(3000 - u, 3000))
    (ht,), cut = split(encoded(3000), limited("head_tail", limit))
    assert cut is True and body(ht) == list(range(u // 2)) + list(range(3000 - u // 2, 3000))
    assert max(len(p.ids) for p in (head, tail, ht)) <= limit


@pytest.mark.parametrize("name", POLICIES)
def test_a_text_within_the_limit_passes_whole(name):
    pieces, cut = split(encoded(254), limited(name, 256))
    assert cut is False and piece_ids(pieces) == [encoded(254).ids.tolist()]
    pieces, cut = split(encoded(800), limited(name, 256))  # under the window, over the limit: now cut
    assert cut is True and sum(len(p.ids) for p in pieces) <= 256


def test_an_empty_limit_is_the_window():
    assert policy("head_tail").limit == WINDOW and limited("head_tail", None).limit == WINDOW
    assert policy("chunks").limit == 8 * WINDOW
    assert policy("chunks", chunk_tokens=512, chunk_max=3).limit == 3 * 512


def test_chunks_total_never_exceeds_the_limit():
    for chunk_tokens, chunk_max in ((1024, 8), (512, 3), (128, 32), (300, 5)):
        for limit in (128, 200, 512, 1024, 1100, 1500, 2048, 3000, 8192):
            for n in (100, 254, 1022, 1023, 2000, 5000, 20_000):
                p = limited("chunks", limit, chunk_tokens=chunk_tokens, chunk_max=chunk_max, overlap=64)
                pieces, cut = split(encoded(n), p)
                lengths = [len(x.ids) for x in pieces]
                case = (chunk_tokens, chunk_max, limit, n, lengths)
                assert sum(lengths) <= limit and len(pieces) <= chunk_max, case
                assert max(lengths) <= min(chunk_tokens, WINDOW) or len(pieces) == 1, case
                covered = set().union(*(body(x) for x in pieces))
                assert cut is (covered != set(range(n))), case
                ends = [body(x)[-1] == n - 1 for x in pieces]
                assert all(length >= 128 for length, end in zip(lengths, ends, strict=True) if not end), case


@pytest.mark.parametrize("limit", [128, 256, 1000, 1024])
@pytest.mark.parametrize("name", ["head", "tail", "head_tail"])
def test_pieces_of_a_full_limit_single(name, limit):
    p = limited(name, limit)
    assert p.piece_lengths(p.limit) == [len(x.ids) for x in split(encoded(50_000), p)[0]]


@pytest.mark.parametrize("limit", [128, 1100, 2100, 2500, 4096, 8192])
def test_pieces_of_a_full_limit_chunks(limit):
    p = limited("chunks", limit)
    assert p.piece_lengths(p.limit) == [len(x.ids) for x in split(encoded(50_000), p)[0]]


def test_pieces_of_a_full_limit_values():
    assert limited("chunks", 2500).piece_lengths(2500) == [1024, 1024, 452]
    assert limited("chunks", 2100).piece_lengths(2100) == [1024, 1024]  # 52 tokens left: not run
    assert policy("head_tail").piece_lengths(1024) == [1024]


def test_the_cut_follows_the_limit():
    text = varied(100_000)
    for p, want in (
        (limited("head", 256), [text[: 8 * 254]]),
        (limited("tail", 256), [text[-8 * 254 :]]),
        (limited("head_tail", 256), [text[: 8 * 127], text[-8 * 127 :]]),
        (limited("chunks", 2048), [text[: 8 * 2048]]),
    ):
        model = CharModel()
        encode(model, text, p)
        assert model.seen == want, p.name


# --- the limit's value: empty, an integer, or auto (docs/40 § 4) -----------------------------------------------------


@pytest.mark.parametrize(
    ("fixed", "per_token", "want", "warning"),
    [(2, 1.39, 256, False), (3, 0.2, 1024, False), (5, 3.0, 128, False), (10, 4.0, 128, True)],
)
def test_auto_is_the_largest_multiple_of_64_within_half_the_deadline(fixed, per_token, want, warning):
    from tantive.engine import resolve_limit

    limit = resolve_limit("auto", policy("head_tail"), CostModel(fixed, per_token), deadline_ms=800)
    assert (limit.effective, limit.source, limit.warning, limit.capped) == (want, "auto", warning, False)
    assert limit.predicted_ms == pytest.approx(fixed + per_token * want)


def test_auto_is_always_a_multiple_of_64():
    from tantive.engine import resolve_limit

    rng = np.random.default_rng(64)
    for _ in range(500):
        cost = CostModel(float(rng.uniform(0, 50)), float(rng.uniform(0.001, 5)))
        name, chunk_tokens, chunk_max = str(rng.choice(POLICIES)), int(rng.integers(128, 1025)), int(rng.integers(1, 9))
        p = policy(name, chunk_tokens=chunk_tokens, chunk_max=chunk_max)
        limit = resolve_limit("auto", p, cost, deadline_ms=int(rng.integers(50, 3000)))
        assert limit.effective % 64 == 0 and 128 <= limit.effective <= p.limit


def test_auto_under_chunks_counts_every_piece():
    from tantive.engine import resolve_limit

    limit = resolve_limit("auto", policy("chunks"), CostModel(10.0, 0.1), deadline_ms=800)
    assert limit.effective == 3584  # pieces 1024, 1024, 1024, 512: 4 × 10 + 0.1 × 3584 = 398.4 ms within 400
    assert limit.predicted_ms == pytest.approx(398.4)


def test_an_integer_is_capped_to_what_the_policy_can_pass():
    from tantive.engine import resolve_limit

    cost = CostModel(1.0, 0.01)
    limit = resolve_limit(5000, policy("head_tail"), cost, deadline_ms=800)
    assert (limit.setting, limit.effective, limit.source, limit.capped) == (5000, 1024, "user", True)
    assert resolve_limit(5000, policy("chunks"), cost, 800).effective == 5000
    assert resolve_limit(9000, policy("chunks"), cost, 800).effective == 8192
    assert resolve_limit(512, policy("head_tail"), cost, 800).capped is False


def test_an_integer_beyond_the_deadline_warns():
    from tantive.engine import resolve_limit

    limit = resolve_limit(512, policy("head_tail"), CostModel(10.0, 2.0), deadline_ms=800)
    assert (limit.effective, limit.warning) == (512, True)
    assert limit.predicted_ms == pytest.approx(1034.0)
    assert resolve_limit(256, policy("head_tail"), CostModel(10.0, 2.0), 800).warning is False


def test_an_empty_limit_is_the_window_as_before():
    from tantive.engine import resolve_limit

    limit = resolve_limit(None, policy("head_tail"), CostModel(1.0, 1.0), deadline_ms=800)
    assert (limit.setting, limit.effective, limit.source, limit.warning) == (None, 1024, "window", True)
    assert resolve_limit(None, policy("chunks"), CostModel(1.0, 0.01), 10_000).effective == 8192


async def test_the_engine_computes_auto_once_and_applies_it(monkeypatch):
    import tantive.engine as engine_module

    calls, runs = [], []
    real = engine_module.resolve_limit
    monkeypatch.setattr(engine_module, "resolve_limit", lambda *a, **k: calls.append(a) or real(*a, **k))
    model = CountingModel(5000)
    model.run_encoded = lambda pieces: runs.append([len(p.ids) for p in pieces]) or [row(0.0) for _ in pieces]
    e = Engine(model, 800, 32, cost=CostModel(2.0, 1.39), max_input_tokens="auto")
    await e.start()
    for _ in range(3):
        await e.classify("long")
    await e.close()
    assert len(calls) == 1
    assert (e.input_limit.effective, e.input_limit.source, e.policy.limit) == (256, "auto", 256)
    assert runs == [[256]] * 3


async def test_an_empty_limit_leaves_the_engine_as_before(fake_model):
    e = Engine(fake_model, 800, 32, cost=CostModel(1.0, 0.01))
    await e.start()
    await e.close()
    assert e.policy.max_input_tokens is None and e.input_limit.source == "window"
