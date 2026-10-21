"""The ONNX model: tokenizer + ONNX Runtime session, 21 outputs decoded into `Outputs` (spec § 3.1, Hub loader parity).

Output 0 is the complexity in tier units (score = center + scale * output, clipped to 0.01 .. 1.00), outputs 1..19
the task logits in TASKS order, output 20 the needs_tools logit. The window is `max_length` tokens; `encode` keeps every
id and `tantive.longprompt` decides what to run when the text is longer (`run` keeps the head, as the Hub loader
does). Window, centre and scale are properties of the model, read from variant.json (`max_length`, `score_center`,
`score_scale`) with the training values as fallback; config.json carries none of them (its `output_layout` is prose).
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Literal, Protocol

import numpy as np

from tantive.tasks import TASKS

MAX_LENGTH = 1024  # the training window; fallback when variant.json does not say
SCORE_CENTER, SCORE_SCALE = 0.5, 0.25  # the training decoding; fallback likewise
DEFAULT_SIGMA = 0.10  # placeholder when variant.json has no sigma; create_app substitutes S1D1_DEFAULT_SIGMA


@dataclass(frozen=True)
class ModelProperties:
    """What decoding needs to know about a variant, from its variant.json."""

    max_length: int = MAX_LENGTH
    score_center: float = SCORE_CENTER
    score_scale: float = SCORE_SCALE

    @classmethod
    def from_variant(cls, variant: dict) -> ModelProperties:
        return cls(
            max_length=int(variant.get("max_length", MAX_LENGTH)),
            score_center=float(variant.get("score_center", SCORE_CENTER)),
            score_scale=float(variant.get("score_scale", SCORE_SCALE)),
        )


@dataclass(frozen=True)
class Outputs:
    complexity_score: float
    task_logits: np.ndarray
    needs_tools_probability: float
    prompt_tokens: int
    truncated: bool
    complexity_output: float = float("nan")  # raw output 0, in tier units (before scaling and clipping)
    needs_tools_logit: float = float("nan")  # raw output 20
    pieces: int = 1  # windows the text was run in (more than one for a long prompt under `chunks`)

    @property
    def task(self) -> str:
        return TASKS[int(np.argmax(self.task_logits))]

    @cached_property
    def task_probabilities(self) -> dict[str, float]:
        """Softmax over the 19 task logits (full precision; answers round when they are built), computed once.
        cached_property writes to the instance __dict__, which a frozen dataclass without slots still has."""
        shifted = np.exp(self.task_logits.astype(np.float64) - np.max(self.task_logits))
        probabilities = shifted / shifted.sum()
        return {name: float(p) for name, p in zip(TASKS, probabilities, strict=True)}


@dataclass(frozen=True)
class ModelInfo:
    variant: str
    revision: str
    sigma: float
    sigma_source: Literal["variant.json", "default"]
    onnx_file: str
    max_length: int = MAX_LENGTH
    score_center: float = SCORE_CENTER
    score_scale: float = SCORE_SCALE


@dataclass(frozen=True)
class Encoded:
    """A tokenised text, ready for the session: the engine encodes once (off the event loop) and runs later.
    `Model.encode` returns every id of the text; the long-prompt policy cuts it into pieces of at most one window
    (`truncated` is then true when some of the text is in no piece)."""

    text: str
    ids: np.ndarray
    mask: np.ndarray
    truncated: bool


class Model(Protocol):
    info: ModelInfo

    def count_tokens(self, text: str) -> int: ...

    def encode(self, text: str) -> Encoded: ...

    def run_encoded(self, encoded: Sequence[Encoded]) -> list[Outputs]: ...

    def run(self, texts: Sequence[str]) -> list[Outputs]: ...


def decode(
    logits: np.ndarray,
    tokens: Sequence[int],
    truncated: Sequence[bool],
    center: float = SCORE_CENTER,
    scale: float = SCORE_SCALE,
) -> list[Outputs]:
    """The Hub loader's decoding of each row of 21 logits (the defaults are the training values, for parity)."""
    results = []
    for row, n, cut in zip(np.asarray(logits, dtype=np.float32), tokens, truncated, strict=True):
        score = float(np.clip(center + scale * row[0], 0.01, 1.0))
        p_tools = float(1 / (1 + np.exp(-row[-1])))
        results.append(
            Outputs(
                complexity_score=round(score, 3),
                task_logits=row[1 : 1 + len(TASKS)].copy(),
                needs_tools_probability=round(p_tools, 3),
                prompt_tokens=int(n),
                truncated=bool(cut),
                complexity_output=float(row[0]),
                needs_tools_logit=float(row[-1]),
            )
        )
    return results


def revision_of(model_dir: str) -> str:
    """The Hub revision the image build wrote next to the weights, else "unknown"."""
    try:
        with open(os.path.join(model_dir, "REVISION"), encoding="utf-8") as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "unknown"


class OnnxModel:
    def __init__(self, model_dir: str, threads: int, inter_op_threads: int) -> None:
        import onnxruntime
        from tokenizers import Tokenizer

        with open(os.path.join(model_dir, "variant.json"), encoding="utf-8") as f:
            variant = json.load(f)
        with open(os.path.join(model_dir, "tokenizer_config.json"), encoding="utf-8") as f:
            pad = json.load(f).get("pad_token", "<pad>")
        properties = ModelProperties.from_variant(variant)
        self.tokenizer = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.tokenizer.no_truncation()  # tokenizer.json may carry one: the long-prompt policy decides what to cut
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id(pad), pad_token=pad)
        map_path = os.path.join(model_dir, "vocab_map.npy")
        self.vocab_map = np.load(map_path) if os.path.isfile(map_path) else None

        onnx_file = variant.get("file", "model.onnx")
        self.session = onnxruntime.InferenceSession(
            os.path.join(model_dir, onnx_file),
            sess_options=session_options(threads, inter_op_threads),
            providers=["CPUExecutionProvider"],
        )
        sigma = variant.get("sigma")
        self.info = ModelInfo(
            variant=_variant_name(variant),
            revision=revision_of(model_dir),
            sigma=float(sigma) if sigma is not None else DEFAULT_SIGMA,
            sigma_source="variant.json" if sigma is not None else "default",
            onnx_file=onnx_file,
            max_length=properties.max_length,
            score_center=properties.score_center,
            score_scale=properties.score_scale,
        )

    def count_tokens(self, text: str) -> int:
        """Tokens one pass over the text sees: the encoded length, capped by the window."""
        return min(len(self.tokenizer.encode(text).ids), self.info.max_length)

    def encode(self, text: str) -> Encoded:
        e = self.tokenizer.encode(text)
        return Encoded(
            text=text,
            ids=np.asarray(e.ids, dtype=np.int64),
            mask=np.asarray(e.attention_mask, dtype=np.int64),
            truncated=False,
        )

    def run_encoded(self, encoded: Sequence[Encoded]) -> list[Outputs]:
        """One session pass over already tokenised texts (pieces of at most one window), padded to the longest."""
        if any(len(e.ids) > self.info.max_length for e in encoded):
            raise ValueError(f"an encoding is longer than the {self.info.max_length}-token window: split it first")
        width = max(len(e.ids) for e in encoded)
        pad_id = self.tokenizer.padding["pad_id"]
        ids = np.full((len(encoded), width), pad_id, dtype=np.int64)
        mask = np.zeros((len(encoded), width), dtype=np.int64)
        for row, e in enumerate(encoded):
            ids[row, : len(e.ids)], mask[row, : len(e.ids)] = e.ids, e.mask
        if self.vocab_map is not None:
            ids = self.vocab_map[ids]
        logits = self.session.run(None, {"input_ids": ids, "attention_mask": mask})[0]
        return decode(
            logits, mask.sum(1), [e.truncated for e in encoded], self.info.score_center, self.info.score_scale
        )

    def run(self, texts: Sequence[str]) -> list[Outputs]:
        """The Hub loader's behaviour: a text beyond the window keeps its head."""
        from tantive.longprompt import LongPromptPolicy, split

        head = LongPromptPolicy("head", window=self.info.max_length)
        return self.run_encoded([split(self.encode(t), head)[0][0] for t in texts])


def session_options(threads: int, inter_op_threads: int):
    """ONNX Runtime session options: thread counts from the settings; optimisation level and execution mode written
    out although they are the defaults, so the place to vary them is known (allow_spinning is left alone)."""
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = inter_op_threads
    options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    return options


def _variant_name(variant: dict) -> str:
    """`variant` from variant.json when present, else derived from the source repo and the recipe."""
    if "variant" in variant:
        return str(variant["variant"])
    name = str(variant.get("source", "s1d1-router")).rsplit("/", 1)[-1] + "-onnx"
    if "int8=True" in str(variant.get("recipe", "")):
        name += "-int8"
    return name
