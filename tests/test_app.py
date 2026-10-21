import pytest

from tantive.app import create_app
from tantive.config import ConfigError


@pytest.fixture
def chat_stub(monkeypatch):
    from fastapi import APIRouter

    from tantive import app as app_module

    def factory(_app):
        r = APIRouter()
        r.add_api_route("/v1/chat/completions", lambda: {"stub": True}, methods=["POST"])
        return r

    monkeypatch.setitem(app_module.ADAPTERS, "chat", factory)


async def test_disabled_format_is_404(chat_stub, client):
    r = await client.post("/v1/chat/completions", json={})
    assert r.status_code == 404
    assert r.json() == {"error": {"message": "Not Found", "type": "invalid_request_error"}}


async def test_enabled_format_is_mounted(chat_stub, fake_model, settings_factory):
    import httpx

    app = create_app(settings_factory(S1D1_FORMATS="chat"), fake_model)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post("/v1/chat/completions", json={})).json() == {"stub": True}


async def test_body_over_2mib_is_413(client):
    r = await client.post(
        "/v1/systemone", content=b"x" * (2 * 1024 * 1024 + 1), headers={"content-type": "application/json"}
    )
    assert r.status_code == 413
    assert r.json()["error"]["type"] == "request_too_large"


async def test_require_api_key_dependency(settings_factory):
    import httpx
    from fastapi import APIRouter, FastAPI

    from tantive.app import install_error_handlers, require_api_key

    app = FastAPI()
    install_error_handlers(app)
    router = APIRouter(dependencies=[require_api_key(settings_factory(S1D1_API_KEY="k"))])
    router.add_api_route("/guarded", lambda: {"ok": True}, methods=["GET"])
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/guarded")).status_code == 401
        wrong = await c.get("/guarded", headers={"Authorization": "Bearer wrong"})
        assert wrong.json() == {"error": {"message": "invalid API key", "type": "authentication_error"}}
        assert (await c.get("/guarded", headers={"Authorization": "Bearer k"})).json() == {"ok": True}
        assert (await c.get("/guarded", headers={"x-api-key": "k"})).status_code == 200


async def test_no_key_configured_means_open(settings_factory):
    import httpx
    from fastapi import APIRouter, FastAPI

    from tantive.app import require_api_key

    app = FastAPI()
    router = APIRouter(dependencies=[require_api_key(settings_factory())])
    router.add_api_route("/guarded", lambda: {"ok": True}, methods=["GET"])
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/guarded")).status_code == 200


async def test_validation_error_is_400_in_the_error_shape():
    import httpx
    from fastapi import FastAPI
    from pydantic import BaseModel

    from tantive.app import install_error_handlers

    class Body(BaseModel):
        text: str

    def echo(body: Body) -> Body:
        return body

    app = FastAPI()
    install_error_handlers(app)
    app.add_api_route("/echo", echo, methods=["POST"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/echo", json={"text": 1})
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"


def test_bad_tiers_is_config_error(fake_model, settings_factory):
    with pytest.raises(ConfigError, match="S1D1_TIERS"):
        create_app(settings_factory(S1D1_TIERS="A=0.5,B=0.4"), fake_model)


async def test_app_state_is_exposed(client, fake_model):
    # the adapters (next tasks) read everything they need from app.state
    app = client.app
    assert app.state.engine.ready
    assert app.state.catalogue.sigma == 0.10
    assert app.state.info.variant == fake_model.info.variant
    assert app.state.info.sigma == 0.10  # S1D1_DEFAULT_SIGMA applied: the FakeModel's variant carries no sigma
    assert "systemone" in app.state.settings.formats


async def _chunks(data: bytes, size: int = 64 * 1024):
    for i in range(0, len(data), size):
        yield data[i : i + size]


async def test_chunked_body_over_2mib_is_413(client):
    r = await client.post(
        "/v1/systemone", content=_chunks(b"x" * (3 * 1024 * 1024)), headers={"content-type": "application/json"}
    )
    assert r.status_code == 413
    assert r.json()["error"]["type"] == "request_too_large"


async def test_chunked_body_under_limit_is_served(client):
    import json

    body = json.dumps(
        {"state": "x" * 10_000, "questions": {"tier": {"type": "choice", "criteria": {"LOW": "", "HIGH": ""}}}}
    ).encode()
    r = await client.post("/v1/systemone", content=_chunks(body, 1024), headers={"content-type": "application/json"})
    assert r.status_code == 200


async def test_default_sigma_applies_when_the_variant_has_none(fake_model, settings_factory):
    import httpx

    app = create_app(settings_factory(S1D1_DEFAULT_SIGMA="0.08"), fake_model)  # FakeModel: sigma_source "default"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            j = (await c.get("/info")).json()
    assert j["sigma"] == 0.08
    assert j["sigma_source"] == "default"
    assert app.state.catalogue.sigma == 0.08


async def test_variant_sigma_wins_over_the_setting(fake_model, settings_factory):
    from dataclasses import replace

    fake_model.info = replace(fake_model.info, sigma=0.12, sigma_source="variant.json")
    app = create_app(settings_factory(S1D1_DEFAULT_SIGMA="0.08"), fake_model)
    assert app.state.catalogue.sigma == 0.12


async def test_ready_line_reports_capacity(fake_model, settings_factory, capsys):
    import json

    from tantive.telemetry import configure_logging

    configure_logging(False)
    app = create_app(settings_factory(S1D1_THREADS="2"), fake_model)
    async with app.router.lifespan_context(app):
        pass
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"event": "ready"' in line]
    ready = lines[-1]
    assert ready["threads"] == 2
    assert ready["cpus"] >= 1
    assert (ready["deadline_ms"], ready["max_queue"]) == (800, 32)
    assert ready["predicted_ms_1024"] >= 0
    assert ready["sustainable_rps"] > 0
    assert "capacity_warning" not in "".join(str(line) for line in lines)


async def test_capacity_warning_when_a_full_window_exceeds_the_deadline(slow_fake, settings_factory, capsys):
    import json

    from tantive.telemetry import configure_logging

    configure_logging(False)
    model = slow_fake(fixed_s=0.001, per_token_s=0.0002)  # 1024 tokens ≈ 206 ms, beyond a 50 ms deadline
    app = create_app(settings_factory(S1D1_DEADLINE_MS="50"), model)
    async with app.router.lifespan_context(app):
        pass
    out = capsys.readouterr().out
    warning = next(json.loads(line) for line in out.splitlines() if '"event": "capacity_warning"' in line)
    assert warning["level"] == "warning"
    assert warning["predicted_ms_1024"] > 50
    assert warning["deadline_ms"] == 50
