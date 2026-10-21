import json
from pathlib import Path

from tests.conftest import outputs
from tests.fixtures.litellm_types import DecisionsResponse, JevSystemOneResponse

FIXTURE = Path(__file__).parent / "fixtures" / "litellm_systemone_request.json"
ASK = "Write a Terraform module for an Azure Container App with internal ingress"


def tier_question(*names: str) -> dict:
    return {"tier": {"type": "choice", "criteria": {n: "" for n in names}}}


async def test_litellm_request_round_trip_validates(client, fake_model):
    req = json.loads(FIXTURE.read_text())
    fake_model.script[ASK] = outputs(score=0.72)
    r = await client.post("/v1/systemone", json=req)
    assert r.status_code == 200
    parsed = JevSystemOneResponse.model_validate(r.json())
    assert parsed.answers["tier"].choice == "COMPLEX"
    assert parsed.usage.input_tokens > 0
    assert parsed.model == "s1d1-fake"
    DecisionsResponse.model_validate(r.json())  # the proxy's generic validation too


async def test_only_the_ask_is_classified(client, fake_model):
    seen = []
    fake_model.on_run = seen.append
    await client.post("/v1/systemone", json=json.loads(FIXTURE.read_text()))
    assert seen == [[ASK]]


async def test_routing_block_and_usage(client):
    r = await client.post("/v1/systemone", json={"state": "x", "questions": tier_question("LOW", "HIGH")})
    j = r.json()
    assert j["usage"] == {"input_tokens": 0, "output_tokens": 0, "pieces": 1}
    assert j["routing"]["complexity_score"] == 0.62
    assert j["routing"]["truncated"] is False
    assert j["routing"]["pieces"] == 1
    assert j["routing"]["latency_ms"] >= 0
    assert j["routing"]["model"] == "s1d1-fake"


async def test_state_object_is_flattened(client, fake_model):
    seen = []
    fake_model.on_run = seen.append
    r = await client.post(
        "/v1/systemone",
        json={
            "state": {"subject": "Refund", "body": "I want my money back"},
            "questions": tier_question("LOW", "HIGH"),
        },
    )
    assert r.status_code == 200
    assert seen == [["subject: Refund\nbody: I want my money back"]]


async def test_empty_ask_400(client):
    r = await client.post(
        "/v1/systemone", json={"state": "ctx\nClassify this message:\n", "questions": tier_question("LOW", "HIGH")}
    )
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert "no text to classify" in r.json()["error"]["message"]


async def test_unknown_question_422_lists_catalogue(client):
    r = await client.post(
        "/v1/systemone",
        json={"state": "x", "questions": {"mood": {"type": "choice", "criteria": {"happy": "", "sad": ""}}}},
    )
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "unknown_question"
    assert r.json()["error"]["message"].startswith("mood: ")
    assert "needs_tools" in r.json()["error"]["message"]


async def test_unknown_question_type_422(client):
    r = await client.post(
        "/v1/systemone", json={"state": "x", "questions": {"q": {"type": "box", "instructions": "?"}}}
    )
    assert r.status_code == 422


async def test_three_questions_at_once(client):
    r = await client.post(
        "/v1/systemone",
        json={
            "state": "x",
            "questions": {
                "tier": {"type": "score", "criteria": ["trivial", "low", "medium", "high", "expert"]},
                "task": {"type": "choice", "criteria": {"writing": "", "coding_debugging": ""}},
                "needs_tools": {"type": "noul", "instructions": "Does it need tools?"},
            },
        },
    )
    j = r.json()
    assert j["answers"]["tier"]["type"] == "score"
    assert j["answers"]["tier"]["legend"]["2"] == "medium"
    assert j["answers"]["task"]["choice"] == "coding_debugging"
    assert j["answers"]["needs_tools"] == {"type": "noul", "noul": 0.2}
    DecisionsResponse.model_validate(j)


async def test_noul_by_criteria_text(client):
    q = {"q": {"type": "noul", "criteria": {"true": "the user needs a tool call", "false": None}}}
    assert (await client.post("/v1/systemone", json={"state": "x", "questions": q})).status_code == 200


async def test_min_confidence_marks_abstention(client):
    r = await client.post(
        "/v1/systemone",
        json={"state": "x", "min_confidence": 0.99, "questions": tier_question("LOW", "MEDIUM", "HIGH")},
    )
    a = r.json()["answers"]["tier"]
    assert a["abstention"] == "abstained"
    assert a["low_confidence"] is True
    assert a["abstention_threshold"] == 0.99


async def test_min_confidence_passed_has_no_flag(client):
    r = await client.post(
        "/v1/systemone", json={"state": "x", "min_confidence": 0.0, "questions": tier_question("LOW", "HIGH")}
    )
    a = r.json()["answers"]["tier"]
    assert a["abstention"] == "passed"
    assert "low_confidence" not in a
    r = await client.post("/v1/systemone", json={"state": "x", "questions": tier_question("LOW", "HIGH")})
    assert "abstention" not in r.json()["answers"]["tier"]


async def test_overloaded_503(client, overloaded_engine):
    r = await client.post("/v1/systemone", json={"state": "x", "questions": tier_question("LOW", "HIGH")})
    assert r.status_code == 503
    assert r.headers["retry-after"] == "1"
    assert r.json()["error"]["type"] == "overloaded"


async def test_systemone_requires_key(client_with_key):
    r = await client_with_key.post("/v1/systemone", json={})
    assert r.status_code == 401
    r = await client_with_key.post(
        "/v1/systemone",
        json={"state": "x", "questions": tier_question("LOW", "HIGH")},
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200


async def test_invalid_body_400(client):
    r = await client.post("/v1/systemone", json={"state": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"


async def test_text_over_50000_chars_413(client):
    r = await client.post("/v1/systemone", json={"state": "x" * 50_001, "questions": tier_question("LOW", "HIGH")})
    assert r.status_code == 413


async def test_over_64_questions_400(client):
    questions = {f"q{i}": {"type": "noul", "instructions": "tool?"} for i in range(65)}
    assert (await client.post("/v1/systemone", json={"state": "x", "questions": questions})).status_code == 400


async def test_batch(client):
    r = await client.post("/v1/systemone/batch", json={"states": ["a", "b"], "questions": tier_question("LOW", "HIGH")})
    assert len(r.json()["results"]) == 2
    assert r.json()["results"][1]["answers"]["tier"]["type"] == "choice"


async def test_batch_over_32_is_400(client):
    r = await client.post("/v1/systemone/batch", json={"states": ["a"] * 33, "questions": tier_question("LOW", "HIGH")})
    assert r.status_code == 400


async def test_decision_is_logged_without_prompt(client, capsys):
    from tantive.telemetry import configure_logging

    configure_logging(False)
    await client.post("/v1/systemone", json={"state": "secret text", "questions": tier_question("LOW", "HIGH")})
    out = capsys.readouterr().out
    assert '"event": "decision"' in out
    assert '"route": "/v1/systemone"' in out
    assert '"tier": "MEDIUM"' in out  # the native tier of the score, whatever the question's names
    assert "secret text" not in out


async def test_single_option_tier_choice_is_422(client):
    r = await client.post("/v1/systemone", json={"state": "x", "questions": tier_question("LOW")})
    assert r.status_code == 422


async def test_scalar_state_is_400(client):
    for state in (None, 42, True):
        r = await client.post("/v1/systemone", json={"state": state, "questions": tier_question("LOW", "HIGH")})
        assert r.status_code == 400, state


async def test_batch_503_mid_way_counts_nothing(client, monkeypatch):
    from tantive.engine import Engine, Overloaded

    calls = []
    original = Engine.classify

    async def second_call_overloaded(self, text):
        calls.append(text)
        if len(calls) == 2:
            raise Overloaded(999.0)
        return await original(self, text)

    monkeypatch.setattr(Engine, "classify", second_call_overloaded)
    before = _decisions_total(await client.get("/metrics"))
    r = await client.post(
        "/v1/systemone/batch", json={"states": ["a", "b", "c"], "questions": tier_question("LOW", "HIGH")}
    )
    assert r.status_code == 503
    assert calls == ["a", "b"]
    assert _decisions_total(await client.get("/metrics")) == before


def _decisions_total(response) -> float:
    return sum(
        float(line.rsplit(" ", 1)[1])
        for line in response.text.splitlines()
        if line.startswith("tantive_decisions_total{") and 'format="systemone"' not in line
    )


async def test_min_confidence_marks_noul_unevaluated(client):
    r = await client.post(
        "/v1/systemone",
        json={"state": "x", "min_confidence": 0.5, "questions": {"q": {"type": "noul", "instructions": "tool?"}}},
    )
    a = r.json()["answers"]["q"]
    assert a["noul"] == 0.2
    assert a["abstention"] == "unevaluated"  # a noul carries no confidence, so the gate cannot decide
    assert a["abstention_threshold"] == 0.5
    assert "low_confidence" not in a
