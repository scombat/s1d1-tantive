"""TEI's /predict shape (spec § 3.5): {inputs: str | [str], raw_scores?, truncate?} → [{score, label}] × 21.

Labels: `complexity`, the 19 task names, `needs_tools`; sorted by score descending. `raw_scores: true` gives the raw
outputs (complexity in tier units, task logits, needs_tools logit), otherwise the decoded values as /classify.
A list of inputs gives a list of lists, per input: an input the single route would refuse gets `{"error"}` in its slot
(422 when every input failed). `truncate` is accepted and ignored: long prompts follow
`S1D1_LONG_PROMPTS`, and the `X-S1D1-Pieces` header gives the windows run.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from tantive.adapters import register
from tantive.adapters.common import (
    MAX_BATCH,
    Core,
    batch_status,
    check_count,
    classify_items,
    classify_text,
    decoded_vector,
    item_error,
    raw_vector,
    record,
)
from tantive.errors import ApiError
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
    async def predict(request: Request, body: PredictRequest) -> JSONResponse:
        core = Core.of(request)
        if isinstance(body.inputs, str):  # one input: refused as a whole, as before
            items = [await classify_text(core, body.inputs, litellm_marker=False)]
        else:
            check_count(body.inputs, MAX_BATCH, "input")
            items = await classify_items(core, body.inputs, litellm_marker=False)
        results: list = []
        for item in items:
            if isinstance(item, ApiError):
                results.append(item_error(item))
                continue
            record(core, ROUTE, FORMAT, item.outputs, item.latency_ms, prompt=item.text)
            values = raw_vector(item.outputs) if body.raw_scores else decoded_vector(item.outputs)
            scored = [{"score": v, "label": label} for v, label in zip(values, LABELS, strict=True)]
            results.append(sorted(scored, key=lambda d: d["score"], reverse=True))
        pieces = sum(i.outputs.pieces for i in items if not isinstance(i, ApiError))
        status, headers = batch_status(items)
        return JSONResponse(
            results if isinstance(body.inputs, list) else results[0],
            status_code=status,
            headers={"X-S1D1-Pieces": str(pieces), **(headers or {})},
        )

    return r
