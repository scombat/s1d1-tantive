async def test_classify_shape(client):
    r = await client.post("/classify", json={"input": ["a", "b"]})
    assert r.status_code == 200
    j = r.json()
    assert j["object"] == "list"
    assert j["id"].startswith("classify-")
    assert j["model"] == "s1d1-fake"
    assert len(j["data"]) == 2
    assert [d["index"] for d in j["data"]] == [0, 1]
    assert j["data"][0]["num_classes"] == 21
    assert len(j["data"][0]["probs"]) == 21
    assert j["data"][0]["label"] == "MEDIUM"
    assert j["data"][0]["probs"][0] == 0.62  # complexity score
    assert j["data"][0]["probs"][9] > 0.99  # coding_debugging, softmax over the 19 tasks
    assert j["data"][0]["probs"][20] == 0.2  # needs_tools probability
    assert j["usage"] == {"prompt_tokens": 0, "total_tokens": 0, "completion_tokens": 0, "pieces": 2}


async def test_classify_single_string(client):
    j = (await client.post("/classify", json={"input": "hello world"})).json()
    assert len(j["data"]) == 1
    assert j["usage"]["prompt_tokens"] == 2


async def test_classify_text_is_taken_as_given(client, fake_model):
    seen = []
    fake_model.on_run = seen.append
    await client.post("/classify", json={"input": "ctx\nClassify this message:\nask"})
    assert seen == [["ctx\nClassify this message:\nask"]]


async def test_classify_over_32_400(client):
    assert (await client.post("/classify", json={"input": ["a"] * 33})).status_code == 400
    assert (await client.post("/classify", json={"input": []})).status_code == 400


async def test_classify_blank_400(client):
    assert (await client.post("/classify", json={"input": " "})).status_code == 400


async def test_classify_list_answers_per_input(client):
    r = await client.post("/classify", json={"input": ["a", " ", "x" * 50_001, "b"]})
    assert r.status_code == 200
    data = r.json()["data"]
    assert [d["index"] for d in data] == [0, 1, 2, 3]
    assert data[0]["label"] == data[3]["label"] == "MEDIUM"
    assert data[1] == {"index": 1, "error": {"message": "no text to classify", "type": "invalid_request_error"}}
    assert data[2]["error"]["type"] == "request_too_large"
    assert r.json()["usage"]["pieces"] == 2  # the inputs that ran


async def test_classify_every_input_failed_is_422(client):
    r = await client.post("/classify", json={"input": ["", " "]})
    assert r.status_code == 422
    assert all("error" in d for d in r.json()["data"])


async def test_classify_list_overloaded_fills_the_slots(client, overloaded_engine):
    r = await client.post("/classify", json={"input": ["x"]})
    assert r.status_code == 422
    assert r.json()["data"][0]["error"]["type"] == "overloaded"


async def test_classify_overloaded_503(client, overloaded_engine):
    assert (await client.post("/classify", json={"input": "x"})).status_code == 503


async def test_long_text_pieces_under_chunks(fake_model, settings_factory):
    """A text of about 3 000 ids runs as three pieces under `chunks`; /classify and the decision log say so."""
    from tantive.app import create_app
    from tests.conftest import _client

    app = create_app(settings_factory(S1D1_LONG_PROMPTS="chunks"), fake_model)
    async for c in _client(app):
        j = (await c.post("/classify", json={"input": "word " * 2400})).json()  # 12 000 chars // 4 = 3 000 ids
    assert j["usage"]["pieces"] == 4
