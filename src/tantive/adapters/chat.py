"""chat-completions (spec § 3.5): the LiteLLM `classifier_type: llm` path (LiteLLM < 1.106) and curl tests.

POST /v1/chat/completions and /chat/completions: the last user message, cut at LiteLLM's marker, is classified; the
tier names allowed by `response_format`'s JSON schema (the `tier` enum) pick the tier set, the native set otherwise;
the answer is `{"tier": "<name>"}` as the assistant message. GET /v1/models and /models list `s1d1-classifier`.
Port of release/s1d1-router/integrations/litellm/serve_litellm.py.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from tantive.adapters import register
from tantive.adapters.common import Core, ask_of, classify, record
from tantive.ask import text_of
from tantive.errors import ApiError
from tantive.tiers import choose, resolve_tier_set

ROUTE, FORMAT = "/v1/chat/completions", "chat"
MODEL_ID = "s1d1-classifier"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    messages: list[dict[str, Any]]
    model: str | None = None
    response_format: Any = None


def last_user_text(messages: list[dict[str, Any]]) -> str:
    """The text of the last user message, or "" without one."""
    user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    return text_of(user.get("content")) if user else ""


def allowed_tiers(response_format: Any) -> list[str] | None:
    """The tier names LiteLLM's JSON schema allows (the enum of the `tier` property), or None when it gives none."""
    if not isinstance(response_format, dict):
        return None
    schema = (response_format.get("json_schema") or {}).get("schema") or {}
    tier = (schema.get("properties") or {}).get("tier") or {}
    if "enum" in tier:
        return list(tier["enum"])
    for ref in tier.get("anyOf", []) + tier.get("allOf", []):  # pydantic may wrap a Literal
        if "enum" in ref:
            return list(ref["enum"])
    if "$ref" in tier:  # or move it to $defs
        name = tier["$ref"].rsplit("/", 1)[-1]
        target = (schema.get("$defs") or schema.get("definitions") or {}).get(name, {})
        if "enum" in target:
            return list(target["enum"])
    return None


def completion(tier: str, prompt_tokens: int) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps({"tier": tier})},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 0, "total_tokens": prompt_tokens},
    }


@register("chat")
def router(_app: FastAPI) -> APIRouter:
    r = APIRouter(tags=["chat"])

    async def chat_completions(request: Request, body: ChatRequest) -> JSONResponse:
        core = Core.of(request)
        allowed = allowed_tiers(body.response_format)
        if allowed is None:
            full = present = core.native
            spelling = {n: n for n in core.native.names}
        else:
            if len(allowed) < 2:
                raise ApiError(400, "a tier choice needs at least two options")
            resolved = resolve_tier_set(allowed, core.native)
            if resolved is None:
                raise ApiError(400, f"tier set {allowed} is not known: set S1D1_TIERS to declare it")
            full, present, spelling = resolved
        text = ask_of(last_user_text(body.messages))
        outputs, latency_ms = await classify(core, text)
        tier, _ = choose(outputs.complexity_score, core.info.sigma, full, present)
        record(core, ROUTE, FORMAT, outputs, latency_ms, allowed=allowed, prompt=text)
        headers = {
            "X-S1D1-Score": str(outputs.complexity_score),
            "X-S1D1-Task": outputs.task,
            "X-S1D1-Needs-Tools": str(outputs.needs_tools_probability),
            "X-S1D1-Pieces": str(outputs.pieces),
            "X-S1D1-Latency-Ms": str(round(latency_ms, 1)),
        }
        return JSONResponse(completion(spelling[tier], outputs.prompt_tokens), headers=headers)

    def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "s1d1"}]}

    for path in (ROUTE, "/chat/completions"):
        r.add_api_route(path, chat_completions, methods=["POST"])
    for path in ("/v1/models", "/models"):
        r.add_api_route(path, models, methods=["GET"])
    return r
