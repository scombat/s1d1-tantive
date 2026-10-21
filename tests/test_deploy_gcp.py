"""The Cloud Run Button files (shape checks only): app.json and a one-line Dockerfile in deploy/gcp."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "deploy" / "gcp"
# Cloud Run: the memory each CPU setting accepts (docs, configuring/services/memory-limits, 2026-10-21), in MiB
MAX_MEMORY_MIB = {"0.08": 512, "0.5": 1024, "1": 4096, "2": 8192, "4": 16384}
VARIANT_MIN_RAM = {"full": 2048, "compact": 2048, "small": 1024, "tiny": 512}


def app() -> dict:
    return json.loads((FOLDER / "app.json").read_text())


def mib(memory: str) -> int:
    value, unit = re.fullmatch(r"(\d+)(Mi|Gi)", memory).groups()
    return int(value) * (1024 if unit == "Gi" else 1)


def test_app_json_names_the_service_and_the_port():
    a = app()
    assert a["name"] == "s1d1-tantive"
    assert a["options"]["port"] == 8788


def test_tiers_are_required_and_not_defaulted():
    tiers = app()["env"]["S1D1_TIERS"]
    assert tiers["description"]
    assert tiers.get("required", True) is True and "value" not in tiers


def test_other_variables_are_defaulted_and_the_key_is_generated():
    env = app()["env"]
    assert env["S1D1_FORMATS"]["value"] == "systemone,decisions,classify"
    assert env["S1D1_DEADLINE_MS"]["value"] == "800"
    assert env["S1D1_API_KEY"]["generator"] == "secret" and "value" not in env["S1D1_API_KEY"]


def test_service_is_private_by_default():
    assert app()["options"]["allow-unauthenticated"] is False


def test_memory_and_cpu_are_a_pair_cloud_run_accepts():
    options = app()["options"]
    assert mib(options["memory"]) <= MAX_MEMORY_MIB[str(options["cpu"])]


def test_dockerfile_is_a_from_on_a_published_image():
    text = (FOLDER / "Dockerfile").read_text()
    image = r"^FROM ghcr\.io/scombat/s1d1-tantive:\d+\.\d+\.\d+-(full|compact|small|tiny)-onnx-int8$"
    assert re.search(image, text, re.M)
    assert not re.search(r"^\s*(COPY|ADD)\b", text, re.M)


def test_variant_fits_the_memory():
    text = (FOLDER / "Dockerfile").read_text()
    variant = re.search(r"-(full|compact|small|tiny)-onnx-int8$", text, re.M).group(1)
    assert mib(app()["options"]["memory"]) >= VARIANT_MIN_RAM[variant]


def test_root_app_json_is_not_used_for_cloud_run():
    # a root app.json belongs to Heroku's button; this one is reached through the dir parameter
    assert (FOLDER / "app.json").exists()
    readme = (FOLDER / "README.md").read_text()
    assert "dir=deploy/gcp" in readme and "Heroku" in readme


def test_readme_states_button_status_and_free_tier():
    text = (FOLDER / "README.md").read_text()
    assert "https://deploy.cloud.run/?git_repo=https://github.com/scombat/s1d1-tantive&dir=deploy/gcp" in text
    assert "https://deploy.cloud.run/button.svg" in text
    assert "- Status: needs public repository" in text
    assert re.search(r"^- RAM: 2048 MiB", text, re.M)
    assert re.search(r"^- Image: `ghcr\.io/scombat/s1d1-tantive:[^`]+-small-onnx-int8`", text, re.M)
    assert "180,000" in text and "360,000" in text and "2026-10-21" in text
    assert "gcloud run deploy" in text and "Artifact Registry" in text


def test_no_secret_value_in_the_folder():
    for path in FOLDER.iterdir():
        assert not re.search(r"(ghp_|BEGIN [A-Z ]*PRIVATE KEY)", path.read_text()), path
