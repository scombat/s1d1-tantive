"""TEI's /predict shape (spec § 3.5): {inputs: str | [str], raw_scores?, truncate?} → [{score, label}] × 21.

Labels: `complexity`, the 19 task names, `needs_tools`; sorted by score descending. `raw_scores: true` gives the raw
outputs (complexity in tier units, task logits, needs_tools logit), otherwise the decoded values as /classify.
A list of inputs gives a list of lists. `truncate` is accepted and ignored: long prompts follow
`S1D1_LONG_PROMPTS`, and the `X-S1D1-Pieces` header gives the windows run.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict

from tantive.adapters import register
from tantive.adapters.common import MAX_BATCH, Core, ask_of, check_count, classify, decoded_vector, raw_vector, record
from tantive.tasks import TASKS

ROUTE, FORMAT = "/predict", "predict"
LABELS = ("complexity", *TASKS, "needs_tools")


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    inputs: str | list[str]
    raw_scores: bool = False
    truncate: bool | None = None


@register("predict")
def router(_app: FastAPI) -> APIRouter:
    r = APIRouter(tags=["predict"])

    @r.post(ROUTE)
    async def predict(request: Request, response: Response, body: PredictRequest) -> list:
        core = Core.of(request)
        inputs = [body.inputs] if isinstance(body.inputs, str) else body.inputs
        check_count(inputs, MAX_BATCH, "input")
        done = []  # all or nothing: a failing input fails the request and nothing is logged or counted
        for raw in inputs:
            text = ask_of(raw, litellm_marker=False)
            outputs, latency_ms = await classify(core, text)
            done.append((text, outputs, latency_ms))
        results = []
        response.headers["X-S1D1-Pieces"] = str(sum(o.pieces for _, o, _ in done))
        for text, outputs, latency_ms in done:
            record(core, ROUTE, FORMAT, outputs, latency_ms, prompt=text)
            values = raw_vector(outputs) if body.raw_scores else decoded_vector(outputs)
            scored = [{"score": v, "label": label} for v, label in zip(values, LABELS, strict=True)]
            results.append(sorted(scored, key=lambda d: d["score"], reverse=True))
        return results if isinstance(body.inputs, list) else results[0]

    return r
