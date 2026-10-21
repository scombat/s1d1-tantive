"""The Heroku Deploy button files (shape checks only): root app.json and heroku.yml, a one-FROM Dockerfile."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "deploy" / "heroku"
# Cedar dyno memory in MiB (devcenter.heroku.com/articles/dyno-types, 2026-10-21; Heroku writes 0.5 GB and 1 GB)
DYNO_RAM = {"eco": 512, "basic": 512, "standard-1x": 512, "standard-2x": 1024, "performance-m": 2560}
VARIANT_MIN_RAM = {"full": 2048, "compact": 2048, "small": 1024, "tiny": 512}


def app() -> dict:
    return json.loads((ROOT / "app.json").read_text())


def dockerfile() -> str:
    return (FOLDER / "Dockerfile").read_text()


def test_app_json_is_a_container_app():
    a = app()
    assert a["stack"] == "container"
    assert len(a["name"]) <= 30 and a["description"]
    assert a["repository"] == "https://github.com/scombat/s1d1-tantive"


def test_tiers_required_other_variables_defaulted_key_generated():
    env = app()["env"]
    assert env["S1D1_TIERS"].get("required", True) is True and "value" not in env["S1D1_TIERS"]
    assert env["S1D1_TIERS"]["description"]
    assert env["S1D1_FORMATS"]["value"] == "systemone,decisions,classify"
    assert env["S1D1_DEADLINE_MS"]["value"] == "800"
    assert env["S1D1_API_KEY"]["generator"] == "secret" and "value" not in env["S1D1_API_KEY"]


def test_formation_is_one_web_dyno_big_enough_for_the_variant():
    web = app()["formation"]["web"]
    assert web["quantity"] == 1
    variant = re.search(r"-(full|compact|small|tiny)-onnx-int8$", dockerfile(), re.M).group(1)
    assert DYNO_RAM[web["size"]] >= VARIANT_MIN_RAM[variant]


def test_no_health_check_key_heroku_has_none_for_containers():
    # Heroku's dyno manager checks containers itself; Dockerfile HEALTHCHECK is unsupported
    assert "healthchecks" not in app()
    assert not re.search(r"^HEALTHCHECK", dockerfile(), re.M)


def test_heroku_yml_builds_the_folder_dockerfile():
    manifest = yaml.safe_load((ROOT / "heroku.yml").read_text())
    assert manifest["build"]["docker"]["web"] == "deploy/heroku/Dockerfile"
    assert "run" not in manifest, "the Dockerfile owns the start command"


def test_dockerfile_is_a_from_on_a_published_image_and_copies_nothing():
    text = dockerfile()
    image = r"^FROM ghcr\.io/scombat/s1d1-tantive:\d+\.\d+\.\d+-(full|compact|small|tiny)-onnx-int8$"
    assert re.search(image, text, re.M)
    assert not re.search(r"^\s*(COPY|ADD)\b", text, re.M)


def test_dockerfile_maps_heroku_port_to_the_service_port():
    # the base image has an ENTRYPOINT, so a CMD alone would be passed to it as arguments: reset the ENTRYPOINT
    text = dockerfile()
    assert re.search(r"^ENTRYPOINT \[.*S1D1_PORT=\$\{?PORT.*python -m tantive", text, re.M)
    assert "exec python -m tantive" in text


def test_readme_states_button_status_and_limits():
    text = (FOLDER / "README.md").read_text()
    assert "https://www.heroku.com/deploy?template=https://github.com/scombat/s1d1-tantive" in text
    assert "https://www.herokucdn.com/deploy/button.svg" in text
    assert "- Status: needs public repository" in text
    assert re.search(r"^- RAM: 1024 MiB", text, re.M)
    assert re.search(r"^- Image: `ghcr\.io/scombat/s1d1-tantive:[^`]+-small-onnx-int8`", text, re.M)
    assert "2026-10-21" in text and "no free" in text.lower() and "standard-2x" in text
    assert "Fir" in text and "/ready" in text


def test_no_secret_value_in_the_files():
    for path in (ROOT / "app.json", ROOT / "heroku.yml", *FOLDER.iterdir()):
        assert not re.search(r"(ghp_|github_pat_|BEGIN [A-Z ]*PRIVATE KEY)", path.read_text()), path
