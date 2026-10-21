"""Prompts longer than the window: how a text becomes pieces, and how the pieces' outputs become one answer.

`Model.encode` returns every id of the text (CLS, the useful ids, SEP). The policy cuts the useful ids on token
boundaries and puts CLS/SEP back around each piece, so a piece never exceeds the window (1 022 useful ids in 1 024
tokens). Up to one window the text is one piece under every policy. The cutting and the merging reproduce
`pieces_of` and `merge` of the research repo's pipeline/11_long_prompts.py, from which the defaults come: `head_tail`
(first 511 and last 511 useful ids, one pass); `chunks` (consecutive windows with an overlap, at most `chunk_max`
of them, the remainder dropped) as an option, merged by `max` for the score, `sum_logits` for the task and `max`
for needs_tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from tantive.model import Encoded, Outputs, decode
from tantive.tasks import TASKS

SCORE_STRATEGIES = ("max", "mean", "median", "first", "length_weighted")
TASK_STRATEGIES = ("sum_logits", "vote")
POLICIES = ("head", "tail", "head_tail", "chunks")


@dataclass(frozen=True)
class LongPromptPolicy:
    name: str = "head_tail"
    window: int = 1024
    chunk_tokens: int = 1024
    overlap: int = 64
    chunk_max: int = 8
    score: str = "max"
    task: str = "sum_logits"

    @classmethod
    def from_settings(cls, settings, window: int) -> LongPromptPolicy:
        return cls(
            settings.long_prompts,
            window,
            settings.chunk_tokens,
            settings.chunk_overlap,
            settings.chunk_max,
            settings.chunk_score,
            settings.chunk_task,
        )

    @property
    def useful(self) -> int:
        """Ids a piece can hold besides CLS and SEP."""
        return self.window - 2


def split(encoded: Encoded, policy: LongPromptPolicy) -> tuple[list[Encoded], bool]:
    """The pieces to run for one encoded text and whether some of its tokens are in none of them. Each piece carries
    that flag, so a lone piece reports it through the model's own outputs."""
    ids = encoded.ids
    if len(ids) <= policy.window:
        return [encoded], False
    bos, eos, body = ids[0], ids[-1], ids[1:-1]
    useful = policy.useful
    if policy.name == "head":
        parts, truncated = [body[:useful]], True
    elif policy.name == "tail":
        parts, truncated = [body[-useful:]], True
    elif policy.name == "head_tail":
        half = useful // 2
        parts, truncated = [np.concatenate([body[:half], body[-half:]])], True
    elif policy.name == "chunks":
        size = min(policy.chunk_tokens, policy.window) - 2
        stride = size - policy.overlap
        if stride < 1:
            raise ValueError("overlap must be smaller than the piece size")
        parts, truncated = [], False
        for start in range(0, len(body), stride):
            parts.append(body[start : start + size])
            if start + size >= len(body):
                break
            if len(parts) == policy.chunk_max:
                truncated = True
                break
    else:
        raise ValueError(f"unknown long-prompt policy {policy.name!r}")
    pieces = []
    for part in parts:
        piece_ids = np.concatenate([[bos], part, [eos]]).astype(np.int64)
        pieces.append(Encoded(encoded.text, piece_ids, np.ones(len(piece_ids), dtype=np.int64), truncated))
    return pieces, truncated


def merge(
    outputs: Sequence[Outputs],
    lengths: Sequence[int],
    score: str,
    task: str,
    center: float | None = None,
    scale: float | None = None,
    truncated: bool = False,
) -> Outputs:
    """One answer from the pieces' outputs, on the raw values: output 0 per `score`; the task logits per `task`
    (`sum_logits`: the summed logits; `vote`: the majority of the pieces' argmax, a tie going to the class with the
    largest logit, as a one-hot row); needs_tools: the largest logit. A single piece is returned as it is."""
    if len(outputs) == 1:
        return outputs[0]
    raw = np.array([o.complexity_output for o in outputs], dtype=np.float64)
    if score == "max":
        s0 = raw.max()
    elif score == "mean":
        s0 = raw.mean()
    elif score == "median":
        s0 = float(np.median(raw))
    elif score == "first":
        s0 = raw[0]
    elif score == "length_weighted":
        s0 = float(np.average(raw, weights=list(lengths)))
    else:
        raise ValueError(f"unknown score strategy {score!r}")
    logits = np.stack([o.task_logits for o in outputs]).astype(np.float64)
    if task == "sum_logits":
        merged = logits.sum(0)
    elif task == "vote":
        votes = np.bincount(logits.argmax(1), minlength=len(TASKS))
        tied = np.flatnonzero(votes == votes.max())
        merged = np.zeros(len(TASKS))
        merged[tied[logits.max(0)[tied].argmax()]] = 1.0
    else:
        raise ValueError(f"unknown task strategy {task!r}")
    tools = max(o.needs_tools_logit for o in outputs)
    row = np.concatenate([[s0], merged, [tools]])[None, :]
    kwargs = {} if center is None else {"center": center, "scale": scale}
    (result,) = decode(row, [sum(lengths)], [truncated], **kwargs)
    return replace(result, pieces=len(outputs))
