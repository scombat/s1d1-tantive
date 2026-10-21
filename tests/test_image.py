"""The variant image, built once per session from the small variant (marker `docker`; skipped without docker)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "tantive:test-small"
VARIANT = "small-onnx-int8"
PORT = 18788

pytestmark = pytest.mark.docker


def run(cmd: str, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check, timeout=timeout, cwd=ROOT)


def build_args(variant: str) -> str:
    """The build args the workflow passes: the Hub repo, commit and model sha256 from the checksums file."""
    c = json.loads((ROOT / "checksums" / f"{variant}.json").read_text())
    return (
        f"--build-arg VARIANT={variant} --build-arg VERSION=test --build-arg GIT_SHA=0000000 "
        f"--build-arg MODEL_REPO={c['repo']} --build-arg MODEL_REVISION={c['revision']} "
        f"--build-arg MODEL_SHA256={c['files']['model_int8.onnx']['sha256']}"
    )


@pytest.fixture(scope="session")
def image() -> str:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    run(f"docker build --target variant {build_args(VARIANT)} -t {IMAGE} .")
    return IMAGE


def wait_ready(url: str, seconds: int) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if urllib.request.urlopen(url, timeout=2).status == 200:
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(1)
    raise AssertionError(f"{url} not ready after {seconds}s")


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


@pytest.fixture
def container(image):
    cid = run(f"docker run -d --read-only --tmpfs /tmp -p {PORT}:8788 {image}").stdout.strip()
    try:
        yield cid
    finally:
        run(f"docker rm -f {cid}", check=False)


def test_image_runs_readonly_nonroot(container):
    wait_ready(f"http://127.0.0.1:{PORT}/ready", 90)
    info = get_json(f"http://127.0.0.1:{PORT}/info")
    assert info["variant"].endswith(VARIANT)
    assert info["revision"] == json.loads((ROOT / "checksums" / f"{VARIANT}.json").read_text())["revision"]
    assert run(f"docker exec {container} id -u").stdout.strip() == "10001"
    body = json.dumps({"state": "hi", "questions": {"tier": {"type": "choice", "criteria": {"LOW": "", "HIGH": ""}}}})
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/systemone", body.encode(), {"content-type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.load(r)["answers"]["tier"]["choice"] in ("LOW", "HIGH")


def test_image_has_no_build_tools(image):
    for cmd in ("uv", "pip", "hf"):
        assert run(f"docker run --rm --entrypoint sh {image} -c 'command -v {cmd}'", check=False).returncode != 0, cmd
    caches = run(f"docker run --rm --entrypoint sh {image} -c 'ls -d /root/.cache /model/.cache 2>/dev/null | wc -l'")
    assert caches.stdout.strip() == "0"
    for module in ("huggingface_hub", "hf_xet"):  # the Hub client and its transfer library are both removed
        assert run(f"docker run --rm --entrypoint python {image} -c 'import {module}'", check=False).returncode != 0


def test_image_verifies_its_own_weights(image):
    r = run(f"docker run --rm --entrypoint python {image} -m tantive.verify /model /model/checksums.json")
    assert r.returncode == 0
    assert "ok:" in r.stdout


def test_image_ships_only_the_listed_files(image):
    listing = run(f"docker run --rm --entrypoint sh {image} -c 'ls /model'").stdout.split()
    expected = set(json.loads((ROOT / "checksums" / f"{VARIANT}.json").read_text())["files"])
    assert set(listing) == expected | {"REVISION", "checksums.json"}


def test_image_labels(image):
    labels = json.loads(run(f"docker inspect -f '{{{{json .Config.Labels}}}}' {image}").stdout)
    c = json.loads((ROOT / "checksums" / f"{VARIANT}.json").read_text())
    assert labels["io.s1d1.model.revision"] == c["revision"]
    assert labels["io.s1d1.model.repo"] == c["repo"]
    assert labels["io.s1d1.model.sha256"] == c["files"]["model_int8.onnx"]["sha256"]
    assert labels["org.opencontainers.image.version"] == "test"
    assert labels["org.opencontainers.image.source"] == "https://github.com/scombat/s1d1-tantive"


def test_image_entrypoint_and_env(image):
    config = json.loads(run(f"docker inspect -f '{{{{json .Config}}}}' {image}").stdout)
    assert config["Entrypoint"] == ["python", "-m", "tantive"]
    assert config["User"] == "10001"
    assert "S1D1_MODEL_DIR=/model" in config["Env"]
    assert "8788/tcp" in config["ExposedPorts"]


def test_dockerfile_has_no_architecture_specific_instruction():
    text = (ROOT / "Dockerfile").read_text()
    assert "linux/amd64" not in text and "linux/arm64" not in text and "--platform" not in text
