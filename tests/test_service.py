import pytest

from tantive.telemetry import configure_logging, log_decision
from tests.conftest import outputs


async def test_health_without_key(client_with_key):
    r = await client_with_key.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_ready_after_start(client):
    assert (await client.get("/ready")).json() == {"ready": True}


async def test_ready_is_503_before_start(fake_model, settings_factory):
    import httpx

    from tantive.app import create_app

    app = create_app(settings_factory(), fake_model)  # lifespan not run: engine not started
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ready")
    assert r.status_code == 503
    assert r.json() == {"ready": False}


async def test_metrics_exposes_series(client):
    from tantive.telemetry import metrics

    with metrics().in_flight():
        metrics().record_request("/v1/test", "test", 0.012, tier="LOW", tokens=12)
    metrics().overloaded()
    text = (await client.get("/metrics")).text
    assert 'tantive_request_latency_seconds_bucket{format="test"' in text
    assert any(line.startswith("tantive_decisions_total{") and 'tier="LOW"' in line for line in text.splitlines())
    assert "tantive_prompt_tokens_bucket" in text
    assert "tantive_overloaded_total" in text
    assert "tantive_in_flight_requests" in text


def test_metrics_is_a_process_singleton():
    from tantive.telemetry import metrics

    assert metrics() is metrics()


async def test_metrics_without_key(client_with_key):
    assert (await client_with_key.get("/metrics")).status_code == 200


async def test_info_reports_tiers_and_formats(client):
    j = (await client.get("/info")).json()
    assert j["service"] == "s1d1-tantive"
    assert j["tiers"] == {"TRIVIAL": 0.15, "LOW": 0.4, "MEDIUM": 0.7, "HIGH": 0.9, "EXPERT": 1.0}
    assert j["formats"] == ["classify", "decisions", "systemone"]
    assert j["sigma"] == 0.10
    assert j["sigma_source"] == "default"
    assert j["variant"] == "fake"
    assert j["deadline_ms"] == 800
    assert j["threads"] == 1
    assert set(j["cost"]) == {"fixed_ms", "per_token_ms"}
    assert j["long_prompts"] == "head_tail"
    assert j["chunks"] == {"tokens": 1024, "overlap": 64, "max": 8, "score": "max", "task": "sum_logits"}


def test_log_decision_has_pieces(capsys):
    configure_logging(False)
    log_decision("/v1/systemone", "systemone", outputs(), "MEDIUM", 3.2, None, None)
    assert '"pieces": 1' in capsys.readouterr().out


def test_log_decision_omits_prompt(capsys):
    configure_logging(False)
    log_decision("/v1/systemone", "systemone", outputs(), "MEDIUM", 3.2, None, "secret text")
    out = capsys.readouterr().out
    assert "secret text" not in out
    assert '"event": "decision"' in out
    assert '"tier": "MEDIUM"' in out


def test_log_decision_includes_prompt_when_enabled(capsys):
    configure_logging(True)
    log_decision("/v1/systemone", "systemone", outputs(), None, 3.2, ["LOW", "HIGH"], "secret text")
    out = capsys.readouterr().out
    assert "secret text" in out
    assert '"allowed": ["LOW", "HIGH"]' in out
    configure_logging(False)


async def test_in_flight_gauge_reads_zero_after_requests(client):
    from tantive.telemetry import metrics

    with metrics().in_flight():
        pass
    text = (await client.get("/metrics")).text
    line = next(line for line in text.splitlines() if line.startswith("tantive_in_flight_requests{"))
    assert line.endswith(" 0.0")


@pytest.fixture
def otlp_endpoint():
    """A closed port as OTLP endpoint; the tracing provider is shut down after the test so that nothing keeps
    retrying an export at process exit."""
    from tantive.telemetry import tracing

    endpoint = "http://127.0.0.1:9"
    yield endpoint
    tracing(endpoint).shutdown()


def test_otlp_url_from_settings_unless_the_standard_variable_is_set(monkeypatch):
    from tantive.telemetry import otlp_url

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert otlp_url("http://otel:4318/", "traces") == "http://otel:4318/v1/traces"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://elsewhere/t")
    assert otlp_url("http://otel:4318", "traces") is None  # the exporter then reads the variable itself


def test_tracing_provider_is_a_process_singleton(fake_model, settings_factory, capsys, otlp_endpoint):
    from opentelemetry import trace

    from tantive.app import create_app
    from tantive.telemetry import configure_logging, tracing

    configure_logging(False)
    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": otlp_endpoint}
    app1 = create_app(settings_factory(**env), fake_model)
    app2 = create_app(settings_factory(**env), fake_model)
    endpoint = env["OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert tracing(endpoint) is tracing(endpoint)
    assert trace.get_tracer_provider() is tracing(endpoint).provider
    assert app1 is not app2
    out = capsys.readouterr().out
    assert "tracing_already_configured" not in out  # same endpoint twice: no complaint
    tracing(None)
    assert "tracing_already_configured" in capsys.readouterr().out
