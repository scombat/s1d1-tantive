"""vLLM's /classify shape (spec § 3.5): {model?, input: str | [str]} → data[{index, label, probs, num_classes}].

`probs` are our 21 decoded values (0 = complexity score, 1..19 = task probabilities, 20 = needs_tools probability):
no softmax over the whole vector, since the first head is a regression. `label` is the native tier. Text is
classified as given (this route is not LiteLLM's: no marker extraction). A list is answered per input: an input the
single route would refuse gets `{"index", "error"}` in its slot; 422 when every input failed.
"""

from __future__ import annotations

import uuid

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
    record,
)
from tantive.errors import ApiError

ROUTE, FORMAT = "/classify", "classify"


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    input: str | list[str]
    model: str | None = None


@register("classify")
def router(_app: FastAPI) -> APIRouter:
    r = APIRouter(tags=["classify"])

    @r.post(ROUTE)
    async def classify_route(request: Request, body: ClassifyRequest) -> JSONResponse:
        core = Core.of(request)
        if isinstance(body.input, str):  # one input: refused as a whole, as before
            items = [await classify_text(core, body.input, litellm_marker=False)]
        else:
            check_count(body.input, MAX_BATCH, "input")
            items = await classify_items(core, body.input, litellm_marker=False)
        data, tokens, pieces = [], 0, 0
        for i, item in enumerate(items):
            if isinstance(item, ApiError):
                data.append({"index": i, **item_error(item)})
                continue
            tier = record(core, ROUTE, FORMAT, item.outputs, item.latency_ms, prompt=item.text)
            tokens += item.outputs.prompt_tokens
            pieces += item.outputs.pieces
            data.append({"index": i, "label": tier, "probs": decoded_vector(item.outputs), "num_classes": 21})
        body_out = {
            "id": f"classify-{uuid.uuid4().hex}",
            "object": "list",
            "model": core.model_name,
            "data": data,
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens, "completion_tokens": 0, "pieces": pieces},
        }
        return JSONResponse(body_out, status_code=batch_status(items))

    return r
