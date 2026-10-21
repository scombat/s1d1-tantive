"""The Render Blueprint (shape checks only): one web service that runs the published image."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = ROOT / "render.yaml"
README = ROOT / "deploy" / "render" / "README.md"
# Render's compute plans (docs/compute-plans, 2026-10-21), plan id -> RAM in MiB
PLAN_RAM = {"free": 512, "0.5c-512mb": 512, "1c-2g": 2048, "2c-4g": 4096}
VARIANT_MIN_RAM = {"full": 2048, "compact": 2048, "small": 1024, "tiny": 512}


def service() -> dict:
    services = yaml.safe_load(BLUEPRINT.read_text())["services"]
    assert len(services) == 1
    return services[0]


def env() -> dict:
    return {item["key"]: item for item in service()["envVars"]}


def test_one_web_service_from_a_public_image():
    s = service()
    assert s["type"] == "web" and s["runtime"] == "image"
    image = r"ghcr\.io/scombat/s1d1-tantive:\d+\.\d+\.\d+-(full|compact|small|tiny)-onnx-int8"
    assert re.fullmatch(image, s["image"]["url"])
    assert "creds" not in s["image"], "the images are public: no registry credential"


def test_readiness_is_the_health_check_and_the_port_is_the_service_port():
    s = service()
    assert s["healthCheckPath"] == "/ready"
    assert str(env()["PORT"]["value"]) == "8788"


def test_tiers_are_asked_for_not_defaulted():
    tiers = env()["S1D1_TIERS"]
    assert tiers["sync"] is False and "value" not in tiers


def test_api_key_is_generated_and_formats_and_deadline_are_set():
    assert env()["S1D1_API_KEY"] == {"key": "S1D1_API_KEY", "generateValue": True}
    assert env()["S1D1_FORMATS"]["value"] == "systemone,decisions,classify"
    assert str(env()["S1D1_DEADLINE_MS"]["value"]) == "800"


def test_pushes_do_not_redeploy_every_copy_made_by_the_button():
    assert service()["autoDeployTrigger"] == "off"


def test_variant_fits_the_plan():
    s = service()
    variant = re.search(r"-(full|compact|small|tiny)-onnx-int8$", s["image"]["url"]).group(1)
    assert PLAN_RAM[s["plan"]] >= VARIANT_MIN_RAM[variant]


def test_no_secret_value_in_the_blueprint():
    assert not re.search(r"(ghp_|BEGIN [A-Z ]*PRIVATE KEY)", BLUEPRINT.read_text())


def test_readme_states_facts_button_and_free_tier_limits():
    text = README.read_text()
    assert "https://render.com/deploy?repo=https://github.com/scombat/s1d1-tantive" in text
    assert "https://render.com/images/deploy-to-render-button.svg" in text
    assert "- Status: needs public repository" in text
    assert re.search(r"^- RAM: 512 MiB", text, re.M)
    assert re.search(r"^- Image: `ghcr\.io/scombat/s1d1-tantive:[^`]+-tiny-onnx-int8`", text, re.M)
    assert "2026-10-21" in text and "15 minutes" in text
