import json
from pathlib import Path

import httpx
import pytest

from tantive.adapters.chat import allowed_tiers, last_user_text
from tantive.app import create_app
from tests.conftest import outputs

CHAT_FIXTURE = Path(__file__).parent / "fixtures" / "litellm_chat_request.json"
WRAPPED = (
    "\nCaller system prompt, quoted as task context:\nYou are a coding agent.\n\n"
    "Classify this message:\nRefactor this parser\nand add tests"
)


@pytest.fixture
async def client_chat(fake_model, settings_factory):
    app = create_app(settings_factory(S1D1_FORMATS="chat"), fake_model)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


def chat(content, **extra) -> dict:
    return {"messages": [{"role": "system", "content": "rubric"}, {"role": "user", "content": content}], **extra}


async def test_chat_litellm_shape(client_chat, fake_model):
    req = json.loads(CHAT_FIXTURE.read_text())
    r = await client_chat.post("/v1/chat/completions", json=req)
    assert r.status_code == 200
    j = r.json()
    assert json.loads(j["choices"][0]["message"]["content"]) == {"tier": "COMPLEX"}
    assert j["object"] == "chat.completion"
    assert j["id"].startswith("chatcmpl-")
    assert j["model"] == "s1d1-classifier"
    assert j["choices"][0]["finish_reason"] == "stop"
    assert j["usage"] == {"prompt_tokens": 8, "completion_tokens": 0, "total_tokens": 8}  # 34 chars // 4
    assert r.headers["x-s1d1-score"] == "0.62"
    assert r.headers["x-s1d1-task"] == "coding_debugging"
    assert r.headers["x-s1d1-needs-tools"] == "0.2"
    assert float(r.headers["x-s1d1-latency-ms"]) >= 0


async def test_chat_only_the_ask_is_classified(client_chat, fake_model):
    seen = []
    fake_model.on_run = seen.append
    await client_chat.post("/v1/chat/completions", json=json.loads(CHAT_FIXTURE.read_text()))
    assert seen == [["Refactor this parser\nand add tests"]]


async def test_chat_plain_user_message_uses_native_set(client_chat):
    r = await client_chat.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}]})
    assert json.loads(r.json()["choices"][0]["message"]["content"]) == {"tier": "MEDIUM"}


async def test_chat_enum_subset_picks_nearest_present(client_chat, fake_model):
    req = json.loads(CHAT_FIXTURE.read_text())
    req["response_format"]["json_schema"]["schema"]["properties"]["tier"]["enum"] = ["SIMPLE", "REASONING"]
    fake_model.script["Refactor this parser\nand add tests"] = outputs(score=0.95)
    r = await client_chat.post("/v1/chat/completions", json=req)
    assert json.loads(r.json()["choices"][0]["message"]["content"]) == {"tier": "REASONING"}


async def test_chat_unknown_enum_400(client_chat):
    req = json.loads(CHAT_FIXTURE.read_text())
    req["response_format"]["json_schema"]["schema"]["properties"]["tier"]["enum"] = ["A", "B"]
    r = await client_chat.post("/v1/chat/completions", json=req)
    assert r.status_code == 400
    assert "S1D1_TIERS" in r.json()["error"]["message"]
    assert "['A', 'B']" in r.json()["error"]["message"]


async def test_chat_no_user_text_400(client_chat):
    body = {"messages": [{"role": "system", "content": "x"}]}
    assert (await client_chat.post("/v1/chat/completions", json=body)).status_code == 400
    assert (
        await client_chat.post("/v1/chat/completions", json=chat("ctx\nClassify this message:\n"))
    ).status_code == 400


async def test_chat_not_a_chat_body_400(client_chat):
    assert (await client_chat.post("/v1/chat/completions", json={"prompt": "x"})).status_code == 400


async def test_chat_unversioned_route_too(client_chat):
    assert (await client_chat.post("/chat/completions", json=chat("hello"))).status_code == 200


async def test_models_list(client_chat):
    for path in ("/v1/models", "/models"):
        j = (await client_chat.get(path)).json()
        assert j == {"object": "list", "data": [{"id": "s1d1-classifier", "object": "model", "owned_by": "s1d1"}]}


async def test_chat_overloaded_503(client_chat, overloaded_engine):
    assert (await client_chat.post("/v1/chat/completions", json=chat("x"))).status_code == 503


async def test_chat_off_by_default(client):
    assert (await client.post("/v1/chat/completions", json=chat("x"))).status_code == 404


async def test_chat_decision_logs_allowed(client_chat, capsys):
    from tantive.telemetry import configure_logging

    configure_logging(False)
    await client_chat.post("/v1/chat/completions", json=json.loads(CHAT_FIXTURE.read_text()))
    out = capsys.readouterr().out
    assert '"allowed": ["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"]' in out
    assert "Refactor this parser" not in out


# --- ported from release/s1d1-router/integrations/litellm/test_serve_litellm.py (the checks that still apply) ---

LITELLM_FORMAT = json.loads(CHAT_FIXTURE.read_text())["response_format"]


def test_last_user_text_from_litellm_payload():
    assert last_user_text([{"role": "system", "content": "rubric"}, {"role": "user", "content": WRAPPED}]) == WRAPPED


def test_last_user_text_from_content_list_skips_images():
    parts = [{"type": "text", "text": WRAPPED}, {"type": "image_url", "image_url": {"url": "x"}}]
    assert last_user_text([{"role": "user", "content": parts}]) == WRAPPED


def test_last_user_text_takes_the_last_user_message():
    assert (
        last_user_text(
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "a"},
                {"role": "user", "content": "second"},
            ]
        )
        == "second"
    )


def test_last_user_text_without_user_message():
    assert last_user_text([{"role": "system", "content": "x"}]) == ""


def test_allowed_tiers_from_enum():
    assert allowed_tiers(LITELLM_FORMAT) == ["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"]


def test_allowed_tiers_from_anyof_and_ref():
    wrapped = {
        "type": "json_schema",
        "json_schema": {"schema": {"properties": {"tier": {"anyOf": [{"enum": ["A", "B"]}]}}}},
    }
    assert allowed_tiers(wrapped) == ["A", "B"]
    ref = {
        "type": "json_schema",
        "json_schema": {
            "schema": {"properties": {"tier": {"$ref": "#/$defs/Tier"}}, "$defs": {"Tier": {"enum": ["C", "D"]}}}
        },
    }
    assert allowed_tiers(ref) == ["C", "D"]


def test_allowed_tiers_absent():
    assert allowed_tiers(None) is None
    assert allowed_tiers({"type": "json_object"}) is None


async def test_chat_single_option_enum_400(client_chat):
    req = json.loads(CHAT_FIXTURE.read_text())
    req["response_format"]["json_schema"]["schema"]["properties"]["tier"]["enum"] = ["MEDIUM"]
    r = await client_chat.post("/v1/chat/completions", json=req)
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "a tier choice needs at least two options"
    assert "S1D1_TIERS" not in r.json()["error"]["message"]


async def test_chat_reports_pieces_in_a_header(client_chat):
    r = await client_chat.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.headers["x-s1d1-pieces"] == "1"


async def test_stream_true_is_400(client_chat):
    body = {"messages": [{"role": "user", "content": "hi"}], "stream": True}
    r = await client_chat.post("/v1/chat/completions", json=body)
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert "does not stream" in r.json()["error"]["message"]
    body["stream"] = False
    assert (await client_chat.post("/v1/chat/completions", json=body)).status_code == 200
