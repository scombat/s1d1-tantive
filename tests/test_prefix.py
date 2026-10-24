"""S1D1_ROOT_PATH: the service answers with and without the prefix, through a real uvicorn server and in isolation."""

import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from starlette.testclient import TestClient

from tantive.app import create_app
from tantive.prefix import PrefixMiddleware, normalise_prefix

BODY = {"state": "x", "questions": {"tier": {"type": "choice", "criteria": {"LOW": "", "HIGH": ""}}}}


@pytest.fixture
def uvicorn_base(fake_model, settings_factory):
    """A real uvicorn server on 127.0.0.1 (free port) serving the app with S1D1_ROOT_PATH=/api."""
    app = create_app(settings_factory(S1D1_ROOT_PATH="/api"), fake_model)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=30)


def test_real_uvicorn_accepts_paths_with_and_without_the_prefix(uvicorn_base):
    with httpx.Client(base_url=uvicorn_base, timeout=30) as c:
        assert c.post("/api/v1/systemone", json=BODY).status_code == 200
        assert c.post("/v1/systemone", json=BODY).status_code == 200
        assert c.get("/ready").status_code == 200
        assert c.get("/api/ready").status_code == 200
        assert c.get("/health").status_code == 200
        assert c.get("/api/metrics").status_code == 200
        assert c.post("/apix/v1/systemone", json=BODY).status_code == 404
        assert c.post("/api/api/v1/systemone", json=BODY).status_code == 404
        assert c.post("/api/v1/nope", json=BODY).status_code == 404


def test_real_uvicorn_without_a_prefix_is_unchanged(fake_model, settings_factory):
    app = create_app(settings_factory(), fake_model)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        port = server.servers[0].sockets[0].getsockname()[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as c:
            assert c.post("/v1/systemone", json=BODY).status_code == 200
            assert c.post("/api/v1/systemone", json=BODY).status_code == 404
    finally:
        server.should_exit = True
        thread.join(timeout=30)


def echo_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request):
        return {"root_path": request.scope["root_path"], "path": request.url.path}

    return app


def test_middleware_strips_the_prefix_once():
    app = PrefixMiddleware(echo_app(), "/api")
    with TestClient(app) as c:
        assert c.get("/api/ping").json() == {"root_path": "/api", "path": "/api/ping"}
        assert c.get("/ping").json() == {"root_path": "", "path": "/ping"}
        assert c.get("/apix/ping").status_code == 404
        assert c.get("/api/api/ping").status_code == 404
        assert c.get("/api").status_code == 404  # the root of the service has no route here, but is not a 500


def test_middleware_normalises_the_prefix():
    assert normalise_prefix("/api/") == "/api"
    assert normalise_prefix("api") == "/api"
    assert normalise_prefix("") == ""
    assert normalise_prefix("/") == ""
    with TestClient(PrefixMiddleware(echo_app(), "api/")) as c:
        assert c.get("/api/ping").status_code == 200


def test_middleware_without_a_prefix_is_a_no_op():
    with TestClient(PrefixMiddleware(echo_app(), "")) as c:
        assert c.get("/ping").status_code == 200
        assert c.get("/api/ping").status_code == 404


def test_middleware_applies_to_websockets():
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"root_path": websocket.scope["root_path"]})
        await websocket.close()

    with TestClient(PrefixMiddleware(app, "/api")) as c:
        with c.websocket_connect("/api/ws") as s:
            assert s.receive_json() == {"root_path": "/api"}
        with c.websocket_connect("/ws") as s:
            assert s.receive_json() == {"root_path": ""}


def test_the_service_app_answers_both_forms(fake_model, settings_factory):
    with TestClient(create_app(settings_factory(S1D1_ROOT_PATH="/api/"), fake_model)) as c:
        assert c.post("/api/v1/systemone", json=BODY).status_code == 200
        assert c.post("/v1/systemone", json=BODY).status_code == 200
        assert c.get("/api/ready").status_code == 200
        assert c.get("/ready").status_code == 200


def test_main_does_not_hand_root_path_to_uvicorn(monkeypatch, fake_model):
    import tantive.model
    from tantive.__main__ import main

    seen = {}
    monkeypatch.setattr(tantive.model, "OnnxModel", lambda *a, **k: fake_model)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    monkeypatch.setenv("S1D1_ROOT_PATH", "/api")
    monkeypatch.setenv("S1D1_THREADS", "1")
    assert main() == 0
    assert "root_path" not in seen
    with TestClient(seen["app"]) as c:
        assert c.post("/api/v1/systemone", json=BODY).status_code == 200
