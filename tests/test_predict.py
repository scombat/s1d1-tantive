import httpx
import pytest

from tantive.app import create_app
from tantive.tasks import TASKS


@pytest.fixture
async def client_predict(fake_model, settings_factory):
    app = create_app(settings_factory(S1D1_FORMATS="predict"), fake_model)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def test_predict_sorted_with_labels(client_predict):
    r = await client_predict.post("/predict", json={"inputs": "x"})
    assert r.status_code == 200
    j = r.json()
    assert len(j) == 21
    assert all(j[i]["score"] >= j[i + 1]["score"] for i in range(20))
    assert {d["label"] for d in j} == {"complexity", "needs_tools", *TASKS}
    assert j[0]["label"] == "coding_debugging"
    assert j[0]["score"] == pytest.approx(0.9940, abs=1e-4)  # softmax of logit 8 against 18 zeros


async def test_predict_reports_pieces_in_a_header(client_predict):
    r = await client_predict.post("/predict", json={"inputs": ["a", "b"]})
    assert r.headers["x-s1d1-pieces"] == "2"


async def test_predict_raw_scores_are_logits(client_predict):
    raw = (await client_predict.post("/predict", json={"inputs": "x", "raw_scores": True})).json()
    cooked = (await client_predict.post("/predict", json={"inputs": "x"})).json()
    assert raw != cooked
    by_label = {d["label"]: d["score"] for d in raw}
    assert by_label["complexity"] == pytest.approx(0.48)  # (0.62 - 0.5) / 0.25: the raw output 0
    assert by_label["coding_debugging"] == 8.0  # the raw logit
    assert by_label["needs_tools"] == pytest.approx(-1.3863, abs=1e-3)  # logit(0.2)


async def test_predict_list_input_gives_list_of_lists(client_predict):
    j = (await client_predict.post("/predict", json={"inputs": ["a", "b"]})).json()
    assert len(j) == 2
    assert len(j[0]) == 21


async def test_predict_over_32_400(client_predict):
    assert (await client_predict.post("/predict", json={"inputs": ["a"] * 33})).status_code == 400


async def test_predict_off_by_default(client):
    assert (await client.post("/predict", json={"inputs": "x"})).status_code == 404


async def test_predict_overloaded_503(client_predict, overloaded_engine):
    assert (await client_predict.post("/predict", json={"inputs": "x"})).status_code == 503
