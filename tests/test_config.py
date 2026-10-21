import pytest

from tantive.config import ConfigError, Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.formats == {"systemone", "decisions", "classify"}
    assert s.deadline_ms == 800
    assert s.max_queue == 32
    assert s.port == 8788
    assert s.long_prompts == "head_tail"
    assert s.model_dir == "/model"
    assert s.inter_op_threads == 1
    assert s.api_key is None
    assert s.log_prompts is False
    assert s.tiers is None
    assert s.otlp_endpoint is None


def test_formats_parsed_and_validated():
    assert Settings.from_env({"S1D1_FORMATS": "chat, predict"}).formats == {"chat", "predict"}


def test_unknown_format_is_config_error():
    with pytest.raises(ConfigError, match="S1D1_FORMATS"):
        Settings.from_env({"S1D1_FORMATS": "grpc"})


def test_threads_default_is_cpu_count(monkeypatch):
    monkeypatch.setattr("tantive.config.cpu_count_from_cgroup", lambda: 4)
    assert Settings.from_env({}).threads == 4


def test_threads_override():
    assert Settings.from_env({"S1D1_THREADS": "2"}).threads == 2


def test_long_prompts_unknown_policy():
    with pytest.raises(ConfigError, match="S1D1_LONG_PROMPTS"):
        Settings.from_env({"S1D1_LONG_PROMPTS": "middle"})


def test_deadline_must_be_positive():
    with pytest.raises(ConfigError, match="S1D1_DEADLINE_MS"):
        Settings.from_env({"S1D1_DEADLINE_MS": "0"})


def test_non_integer_is_config_error():
    with pytest.raises(ConfigError, match="S1D1_PORT"):
        Settings.from_env({"S1D1_PORT": "http"})


def test_log_prompts_flag_and_otlp_endpoint():
    s = Settings.from_env({"S1D1_LOG_PROMPTS": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel:4318"})
    assert s.log_prompts is True
    assert s.otlp_endpoint == "http://otel:4318"


def test_cpu_count_from_cgroup_reads_cpu_max(tmp_path, monkeypatch):
    from tantive import config

    (tmp_path / "cpu.max").write_text("150000 100000\n")
    monkeypatch.setattr(config, "CGROUP_CPU_MAX", str(tmp_path / "cpu.max"))
    assert config.cpu_count_from_cgroup() == 2


def test_cpu_count_from_cgroup_falls_back(tmp_path, monkeypatch):
    from tantive import config

    monkeypatch.setattr(config, "CGROUP_CPU_MAX", str(tmp_path / "missing"))
    monkeypatch.setattr("os.cpu_count", lambda: 6)
    assert config.cpu_count_from_cgroup() == 6


def test_default_sigma_setting():
    assert Settings.from_env({}).default_sigma == 0.10
    assert Settings.from_env({"S1D1_DEFAULT_SIGMA": "0.08"}).default_sigma == 0.08
    with pytest.raises(ConfigError, match="S1D1_DEFAULT_SIGMA"):
        Settings.from_env({"S1D1_DEFAULT_SIGMA": "0"})
    with pytest.raises(ConfigError, match="S1D1_DEFAULT_SIGMA"):
        Settings.from_env({"S1D1_DEFAULT_SIGMA": "wide"})


def test_capture_defaults_to_off():
    s = Settings.from_env({})
    assert s.capture_dir is None
    assert (s.capture_sample, s.capture_min_margin, s.capture_max_mb) == (1.0, None, 100)
    assert (s.capture_retention_days, s.capture_queue) == (None, 10000)


def test_capture_settings_are_read():
    s = Settings.from_env(
        {
            "S1D1_CAPTURE_DIR": "/capture",
            "S1D1_CAPTURE_SAMPLE": "0.25",
            "S1D1_CAPTURE_MIN_MARGIN": "0.05",
            "S1D1_CAPTURE_MAX_MB": "5",
            "S1D1_CAPTURE_RETENTION_DAYS": "30",
            "S1D1_CAPTURE_QUEUE": "50",
        }
    )
    assert s.capture_dir == "/capture"
    assert (s.capture_sample, s.capture_min_margin, s.capture_max_mb) == (0.25, 0.05, 5)
    assert (s.capture_retention_days, s.capture_queue) == (30, 50)


@pytest.mark.parametrize(
    "name,value",
    [
        ("S1D1_CAPTURE_SAMPLE", "1.5"),
        ("S1D1_CAPTURE_SAMPLE", "x"),
        ("S1D1_CAPTURE_MIN_MARGIN", "-0.1"),
        ("S1D1_CAPTURE_MAX_MB", "0"),
        ("S1D1_CAPTURE_RETENTION_DAYS", "0"),
        ("S1D1_CAPTURE_QUEUE", "0"),
    ],
)
def test_capture_settings_are_validated(name, value):
    with pytest.raises(ConfigError, match=name):
        Settings.from_env({name: value})
