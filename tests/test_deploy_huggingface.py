"""The Hugging Face Docker Space files (shape checks only): the Space card, its Dockerfile and the publish steps."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "deploy" / "huggingface"
IMAGE = re.compile(r"^FROM ghcr\.io/scombat/s1d1-tantive:\d+\.\d+\.\d+-(full|compact|small|tiny)-onnx-int8$", re.M)


def front_matter() -> dict:
    text = (FOLDER / "README.md").read_text()
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, "the Space card starts with a YAML front matter block"
    return yaml.safe_load(match.group(1))


def dockerfile() -> str:
    return (FOLDER / "Dockerfile").read_text()


def test_space_card_is_a_docker_space_on_the_service_port():
    meta = front_matter()
    assert meta["sdk"] == "docker"
    assert meta["app_port"] == 8788
    assert meta["title"] and meta["emoji"]


def test_space_card_points_to_the_licence():
    meta = front_matter()
    assert meta["license"] == "other"
    assert "BSL" in (FOLDER / "README.md").read_text()


def test_space_card_suggests_the_free_hardware():
    assert front_matter()["suggested_hardware"] == "cpu-basic"


def test_dockerfile_pulls_a_published_image_and_copies_nothing():
    text = dockerfile()
    assert IMAGE.search(text), "FROM a ghcr.io/scombat/s1d1-tantive:<version>-<variant> tag"
    assert not re.search(r"^\s*(COPY|ADD)\b", text, re.M)


def test_dockerfile_variant_fits_the_free_cpu_tier():
    # CPU Basic has 16 GB: the full variant fits
    assert "-full-onnx-int8" in dockerfile()


def test_dockerfile_runs_as_the_user_hugging_face_uses():
    # Docker Spaces run containers as uid 1000
    assert re.search(r"^USER 1000$", dockerfile(), re.M)


def test_dockerfile_sets_the_service_variables_without_secrets():
    text = dockerfile()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for name in ("S1D1_FORMATS", "S1D1_TIERS", "S1D1_DEADLINE_MS"):
        assert f"{name}=" in code, name
    assert "S1D1_API_KEY" not in code
    assert re.search(r"^EXPOSE 8788$", text, re.M)


def test_publish_steps_name_the_space_the_secret_and_the_paid_plan():
    text = (FOLDER / "PUBLISH.md").read_text()
    assert "scombat/s1d1-tantive" in text
    assert "S1D1_API_KEY" in text and "secret" in text.lower()
    assert "paid plan" in text.lower()
    assert "hf " in text


def test_no_secret_value_in_the_folder():
    for path in (p for p in FOLDER.rglob("*") if p.is_file()):
        assert not re.search(r"(ghp_|hf_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY)", path.read_text()), path


def test_publish_sets_the_api_key_before_the_first_upload():
    text = " ".join((FOLDER / "PUBLISH.md").read_text().split())
    assert "before the first upload" in text
    assert text.index("S1D1_API_KEY") < text.index("hf upload")
