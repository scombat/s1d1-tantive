"""vLLM's /classify shape (spec § 3.5): {model?, input: str | [str]} → data[{index, label, probs, num_classes}].

`probs` are our 21 decoded values (0 = complexity score, 1..19 = task probabilities, 20 = needs_tools probability):
no softmax over the whole vector, since the first head is a regression. `label` is the native tier. Text is
classified as given (this route is not LiteLLM's: no marker extraction).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel, ConfigDict

from tantive.adapters import register
from tantive.adapters.common import MAX_BATCH, Core, ask_of, check_count, classify, decoded_vector, record

ROUTE, FORMAT = "/classify", "classify"


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    input: str | list[str]
    model: str | None = None


@register("classify")
def router(_app: FastAPI) -> APIRouter:
    r = APIRouter(tags=["classify"])

    @r.post(ROUTE)
    async def classify_route(request: Request, body: ClassifyRequest) -> dict:
        core = Core.of(request)
        inputs = [body.input] if isinstance(body.input, str) else body.input
        check_count(inputs, MAX_BATCH, "input")
        done = []  # all or nothing: a failing input fails the request and nothing is logged or counted
        for raw in inputs:
            text = ask_of(raw, litellm_marker=False)
            outputs, latency_ms = await classify(core, text)
            done.append((text, outputs, latency_ms))
        data, tokens, pieces = [], 0, 0
        for i, (text, outputs, latency_ms) in enumerate(done):
            tier = record(core, ROUTE, FORMAT, outputs, latency_ms, prompt=text)
            tokens += outputs.prompt_tokens
            pieces += outputs.pieces
            data.append({"index": i, "label": tier, "probs": decoded_vector(outputs), "num_classes": 21})
        return {
            "id": f"classify-{uuid.uuid4().hex}",
            "object": "list",
            "model": core.model_name,
            "data": data,
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens, "completion_tokens": 0, "pieces": pieces},
        }

    return r
