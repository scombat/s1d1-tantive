import importlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from tantive.model import OnnxModel, decode, revision_of
from tantive.tasks import TASKS


def test_tasks_are_the_hub_nineteen():
    assert len(TASKS) == 19
    assert TASKS[0] == "information_seeking"
    assert TASKS[8] == "coding_debugging"
    assert TASKS[-1] == "other"


def test_decode_matches_hub_formula():
    row = np.array([[0.48] + [0.0] * 8 + [3.0] + [0.0] * 10 + [0.0]], dtype=np.float32)
    out = decode(row, [12], [False])[0]
    assert out.complexity_score == 0.62
    assert out.task == "coding_debugging"
    assert out.needs_tools_probability == 0.5
    assert out.prompt_tokens == 12
    assert out.truncated is False
    assert out.complexity_output == pytest.approx(0.48)
    assert out.needs_tools_logit == 0.0


def test_decode_clips_score():
    assert decode(np.array([[5.0] + [0.0] * 20]), [1], [False])[0].complexity_score == 1.0
    assert decode(np.array([[-5.0] + [0.0] * 20]), [1], [False])[0].complexity_score == 0.01


def test_task_probabilities_sum_to_one():
    out = decode(np.zeros((1, 21)), [1], [False])[0]
    p = out.task_probabilities
    assert abs(sum(p.values()) - 1) < 1e-6
    assert len(p) == 19
    assert out.task_probabilities is p  # computed once, cached on the (frozen) instance


def test_fake_model_contract(fake_model):
    assert fake_model.count_tokens("abcd" * 3) == 3
    assert fake_model.run(["x"])[0].complexity_score == 0.62
    assert fake_model.info.sigma == 0.10


def test_revision_of_reads_file_or_unknown(tmp_path):
    assert revision_of(str(tmp_path)) == "unknown"
    (tmp_path / "REVISION").write_text("abc123\n")
    assert revision_of(str(tmp_path)) == "abc123"


# --- parity with the Hub loader (slow; needs S1D1_TEST_MODEL_DIR and S1D1_TEST_HUB_LOADER) ---

PARAGRAPH = (
    "The Container Apps environment is integrated with the virtual network, so the internal ingress of the "
    "classifier is reachable from LiteLLM only; the replicas scale on concurrent HTTP requests. "
)
PROMPTS = [
    "hi",
    "Quelle est la capitale de l'Australie ?",
    "Write a Terraform module for an Azure Container App with internal ingress, managed identity and secrets.",
    "Corrige ce code : def f(x): return x +\nIl manque quelque chose.",
    "请把这段话翻译成法语：机器学习模型需要大量数据。",
    "Prove that there are infinitely many primes, then explain why the proof is constructive or not.",
    "Résume en trois phrases l'article suivant sur la politique monétaire de la BCE en 2025.",
    "What tools would you call to book a flight from Paris to Tokyo next Monday and add it to my calendar?",
    "Tell me a joke about cats.",
    PARAGRAPH * 60,  # well over 1024 tokens: truncated
]


@pytest.fixture
def model_dir() -> str:
    path = os.environ.get("S1D1_TEST_MODEL_DIR")
    if not path:
        pytest.skip("S1D1_TEST_MODEL_DIR not set")
    return path


@pytest.mark.slow
def test_parity_with_hub_loader(model_dir):
    loader_dir = os.environ.get("S1D1_TEST_HUB_LOADER")
    if not loader_dir:
        pytest.skip("S1D1_TEST_HUB_LOADER not set")
    sys.path.insert(0, loader_dir)
    try:
        hub = importlib.import_module("s1d1_router")
    finally:
        sys.path.remove(loader_dir)
    ref = hub.Router(model_dir).route(PROMPTS)
    model = OnnxModel(model_dir, threads=4, inter_op_threads=1)
    ours = model.run(PROMPTS)
    for r, o in zip(ref, ours, strict=True):
        assert abs(r["complexity_score"] - o.complexity_score) <= 0.001
        assert r["task"] == o.task
        assert abs(r["needs_tools_probability"] - o.needs_tools_probability) <= 0.001
        assert r["prompt_tokens"] == o.prompt_tokens
    assert [o.truncated for o in ours] == [False] * 9 + [True]
    encoded = [model.encode(p) for p in PROMPTS]
    # encode keeps every id: the policy, not the tokenizer, decides what a long text runs as
    assert [len(e.ids) for e in encoded[:-1]] == [o.prompt_tokens for o in ours[:-1]]
    assert len(encoded[-1].ids) > 1024 and encoded[-1].truncated is False
    from tantive.longprompt import LongPromptPolicy, split

    head, cut = split(encoded[-1], LongPromptPolicy("head", window=1024))
    assert cut is True and len(head[0].ids) == 1024
    again = model.run_encoded([e if len(e.ids) <= 1024 else head[0] for e in encoded])
    assert [a.complexity_score for a in again] == [o.complexity_score for o in ours]
    assert ours[-1].prompt_tokens == 1024
    assert model.count_tokens(PROMPTS[-1]) == 1024
    # the Hub's variant.json carries sigma since 2026-10-20; older snapshots fall back to the default
    sigma = json.loads((Path(model_dir) / "variant.json").read_text()).get("sigma")
    if sigma is None:
        assert model.info.sigma_source == "default" and model.info.sigma == 0.10
    else:
        assert model.info.sigma_source == "variant.json" and model.info.sigma == sigma
    assert (model.info.max_length, model.info.score_center, model.info.score_scale) == (1024, 0.5, 0.25)


def test_decode_takes_center_and_scale():
    row = np.array([[1.0] + [0.0] * 20], dtype=np.float32)
    assert decode(row, [1], [False])[0].complexity_score == 0.75  # defaults 0.5 + 0.25 * 1
    assert decode(row, [1], [False], center=0.2, scale=0.1)[0].complexity_score == 0.3


def test_model_properties_from_variant_json_with_fallbacks():
    from tantive.model import ModelProperties

    assert ModelProperties.from_variant({}) == ModelProperties(max_length=1024, score_center=0.5, score_scale=0.25)
    p = ModelProperties.from_variant({"max_length": 512, "score_center": 0.4, "score_scale": 0.2, "sigma": 0.08})
    assert p == ModelProperties(max_length=512, score_center=0.4, score_scale=0.2)


def test_session_options_are_explicit():
    import onnxruntime

    from tantive.model import session_options

    o = session_options(threads=3, inter_op_threads=2)
    assert (o.intra_op_num_threads, o.inter_op_num_threads) == (3, 2)
    assert o.graph_optimization_level == onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
    assert o.execution_mode == onnxruntime.ExecutionMode.ORT_SEQUENTIAL


@pytest.mark.slow
def test_long_prompt_policies_on_real_tokens(model_dir):
    from tantive.longprompt import LongPromptPolicy, split

    model = OnnxModel(model_dir, threads=4, inter_op_threads=1)
    full = model.encode(PROMPTS[-1])
    body = full.ids[1:-1].tolist()
    (ht,), cut = split(full, LongPromptPolicy("head_tail", window=1024))
    assert cut and ht.ids[1:-1].tolist() == body[:511] + body[-511:]
    assert (ht.ids[0], ht.ids[-1]) == (full.ids[0], full.ids[-1])
    pieces, _ = split(full, LongPromptPolicy("chunks", window=1024))
    outs = model.run_encoded(pieces)  # one pass, every piece within the window
    assert len(outs) == len(pieces) > 1
