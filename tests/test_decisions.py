from tests.fixtures.litellm_types import OpenAIDecisionResponse

LITELLM_TIERS = [{"value": v} for v in ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")]


async def test_decisions_choice_validates_against_litellm_types(client):
    r = await client.post(
        "/v1/decisions",
        json={
            "input": "Write a sorting function",
            "questions": [{"type": "choice", "name": "tier", "instructions": "Which tier?", "choices": LITELLM_TIERS}],
        },
    )
    assert r.status_code == 200
    p = OpenAIDecisionResponse.model_validate(r.json())
    assert p.answers[0].name == "tier"
    assert p.answers[0].choice == "COMPLEX"
    assert [c.value for c in p.answers[0].probabilities] == ["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"]
    assert p.usage.total_tokens == p.usage.input_tokens
    assert p.usage.output_tokens == 0
    assert p.model == "s1d1-fake"
    assert r.json()["usage"]["pieces"] == 1


async def test_decisions_messages_input(client, fake_model):
    seen = []
    fake_model.on_run = seen.append
    r = await client.post(
        "/v1/decisions",
        json={
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "ctx\nClassify this message:\nreal ask"}],
                }
            ],
            "questions": [{"type": "predicate", "instructions": "Does it need a tool?"}],
        },
    )
    assert r.status_code == 200
    assert seen == [["real ask"]]
    a = r.json()["answers"][0]
    assert a == {"type": "predicate", "name": None, "probability": 0.2}


async def test_decisions_several_messages_are_joined(client, fake_model):
    seen = []
    fake_model.on_run = seen.append
    await client.post(
        "/v1/decisions",
        json={
            "input": [{"type": "message", "role": "user", "content": "a"}, {"type": "message", "content": "b"}],
            "questions": [{"type": "predicate", "name": "needs_tools", "instructions": "?"}],
        },
    )
    assert seen == [["a\nb"]]


async def test_decisions_score_probabilities_are_indexed(client):
    r = await client.post(
        "/v1/decisions",
        json={
            "input": "x",
            "questions": [
                {"type": "score", "instructions": "How hard?", "levels": [{"label": "easy"}, {"label": "hard"}]}
            ],
        },
    )
    a = r.json()["answers"][0]
    assert [p["value"] for p in a["probabilities"]] == [0, 1]
    assert a["probabilities"][1]["label"] == "hard"
    assert 0 <= a["score"] <= 1
    OpenAIDecisionResponse.model_validate(r.json())


async def test_decisions_answers_keep_question_order(client):
    r = await client.post(
        "/v1/decisions",
        json={
            "input": "x",
            "questions": [
                {"type": "predicate", "name": "tools", "instructions": "?"},
                {"type": "choice", "name": "tier", "instructions": "?", "choices": LITELLM_TIERS},
            ],
        },
    )
    assert [a["type"] for a in r.json()["answers"]] == ["predicate", "choice"]


async def test_decisions_unknown_422(client):
    r = await client.post(
        "/v1/decisions",
        json={
            "input": "x",
            "questions": [{"type": "choice", "instructions": "?", "choices": [{"value": "red"}, {"value": "blue"}]}],
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "unknown_question"


async def test_decisions_box_question_422(client):
    r = await client.post(
        "/v1/decisions", json={"input": "x", "questions": [{"type": "box", "instructions": "the mug"}]}
    )
    assert r.status_code == 422


async def test_decisions_question_count_bounds(client):
    assert (await client.post("/v1/decisions", json={"input": "x", "questions": []})).status_code == 400
    many = [{"type": "predicate", "instructions": "tool?"}] * 129
    assert (await client.post("/v1/decisions", json={"input": "x", "questions": many})).status_code == 400


async def test_decisions_empty_input_400(client):
    r = await client.post(
        "/v1/decisions", json={"input": "   ", "questions": [{"type": "predicate", "instructions": "tool?"}]}
    )
    assert r.status_code == 400


async def test_decisions_overloaded_503(client, overloaded_engine):
    r = await client.post(
        "/v1/decisions", json={"input": "x", "questions": [{"type": "predicate", "instructions": "tool?"}]}
    )
    assert r.status_code == 503
    assert r.headers["retry-after"] == "1"


async def test_decisions_disabled_404(fake_model, settings_factory):
    import httpx

    from tantive.app import create_app

    app = create_app(settings_factory(S1D1_FORMATS="systemone"), fake_model)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post("/v1/decisions", json={})).status_code == 404
