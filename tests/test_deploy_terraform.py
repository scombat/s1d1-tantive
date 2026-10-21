"""The Terraform file of each target: one main.tf, formatted, valid, pinned, and consistent with the target's Facts."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
# the main.tf of each target, and the folder whose README "Facts" names the image it runs
TARGETS = {
    "azure": ("azure/terraform", "azure"),
    "aws-ecs": ("aws/terraform/ecs", "aws"),
    "aws-lambda": ("aws/terraform/lambda", "aws"),
    "gcp": ("gcp/terraform", "gcp"),
    "heroku": ("heroku/terraform", "heroku"),
    "render": ("render/terraform", "render"),
}
PROVIDERS = {
    "azure": "hashicorp/azurerm",
    "aws-ecs": "hashicorp/aws",
    "aws-lambda": "hashicorp/aws",
    "gcp": "hashicorp/google",
    "heroku": "heroku/heroku",
    "render": "render-oss/render",
}
IDS = list(TARGETS)


def folder(target: str) -> Path:
    return DEPLOY / TARGETS[target][0]


def main_tf(target: str) -> str:
    return (folder(target) / "main.tf").read_text()


def facts_image(target: str) -> str:
    readme = (DEPLOY / TARGETS[target][1] / "README.md").read_text()
    return re.search(r"^- Image: `([^`]+)`", readme, re.M).group(1)


def variable_block(text: str, name: str) -> str:
    match = re.search(rf'^variable "{name}" \{{\n(.*?)^\}}', text, re.S | re.M)
    assert match, f"variable {name} is declared"
    return match.group(1)


def test_every_target_has_exactly_one_file():
    for target in IDS:
        assert [p.name for p in folder(target).iterdir() if p.suffix == ".tf"] == ["main.tf"], target


@pytest.mark.parametrize("target", IDS)
def test_tiers_is_a_required_variable(target):
    block = variable_block(main_tf(target), "s1d1_tiers")
    assert "default" not in block and "description" in block
    assert re.search(r'"S1D1_TIERS"|S1D1_TIERS\s*=', main_tf(target))


@pytest.mark.parametrize("target", IDS)
def test_port_and_image_match_the_facts(target):
    text = main_tf(target)
    assert "8788" in text
    assert facts_image(target) in text and ":0.6.0-" in facts_image(target)


@pytest.mark.parametrize("target", IDS)
def test_providers_are_pinned_and_there_is_no_module_nor_backend(target):
    text = main_tf(target)
    assert re.search(r"required_version\s*=\s*\"~?>= ?1\.\d+", text)
    assert re.search(rf'source\s*=\s*"{PROVIDERS[target]}"\s*\n\s*version\s*=\s*"~> \d+\.\d+"', text)
    assert not re.search(r"^\s*(module|backend)\b", text, re.M)


@pytest.mark.parametrize("target", IDS)
def test_variables_are_described_and_there_is_an_url_output(target):
    text = main_tf(target)
    names = re.findall(r'^variable "(\w+)"', text, re.M)
    assert names
    for name in names:
        assert "description" in variable_block(text, name), name
    assert re.search(r'^output "url"', text, re.M)


@pytest.mark.parametrize("target", IDS)
def test_the_api_key_is_a_sensitive_optional_variable(target):
    block = variable_block(main_tf(target), "s1d1_api_key")
    assert re.search(r"sensitive\s*=\s*true", block) and re.search(r'default\s*=\s*""', block)


@pytest.mark.parametrize("target", IDS)
def test_no_secret_value_in_the_file(target):
    assert not re.search(r"(ghp_|github_pat_|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY)", main_tf(target))


def test_huggingface_says_why_there_is_no_file():
    text = (DEPLOY / "huggingface/terraform/README.md").read_text()
    assert len(text.strip().splitlines()) == 1 and "provider" in text and "official" in text


@pytest.mark.parametrize("target", IDS)
def test_the_readme_points_to_the_file(target):
    readme = (DEPLOY / TARGETS[target][1] / "README.md").read_text()
    assert f"deploy/{TARGETS[target][0]}/main.tf" in readme
    assert len(readme.splitlines()) <= 130


# ---- terraform itself (needs the binary; `init` downloads the provider from the registry) ----------------------------


@pytest.fixture(scope="session")
def terraform_env(tmp_path_factory):
    if shutil.which("terraform") is None:
        pytest.skip("terraform is not installed")
    cache = Path(os.environ.get("TF_PLUGIN_CACHE_DIR") or Path.home() / ".terraform.d" / "plugin-cache")
    cache.mkdir(parents=True, exist_ok=True)
    return {**os.environ, "TF_PLUGIN_CACHE_DIR": str(cache), "TF_IN_AUTOMATION": "1"}


def run(command: str, cwd: Path, env: dict, timeout: int = 900):
    return subprocess.run(command, shell=True, capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout)


@pytest.mark.parametrize("target", IDS)
def test_terraform_fmt_check(target, terraform_env):
    done = run("terraform fmt -check -diff", folder(target), terraform_env)
    assert done.returncode == 0, done.stdout + done.stderr


# init downloads the provider and validate starts it. aws (600 MB), azurerm and google take minutes on a first run
# (and far longer under Rosetta with an Intel terraform on an arm64 Mac): those are `slow`, run them with
# `uv run pytest -m slow -k terraform`. The light providers validate in the default selection.
HEAVY = {"azure", "aws-ecs", "aws-lambda", "gcp"}
VALIDATE = [pytest.param(t, marks=pytest.mark.slow) if t in HEAVY else t for t in IDS]


@pytest.mark.parametrize("target", VALIDATE)
def test_terraform_validate(target, terraform_env, tmp_path):
    work = tmp_path / target  # a copy: init writes .terraform and a lock file next to the file
    shutil.copytree(folder(target), work)
    init = run("terraform init -backend=false -input=false", work, terraform_env)
    assert init.returncode == 0, init.stdout + init.stderr
    done = run("terraform validate", work, terraform_env)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("target", IDS)
def test_trivy_config_clean_on_terraform(target):
    if shutil.which("trivy") is None:
        pytest.skip("trivy is not installed")
    command = f"trivy config --exit-code 1 --severity HIGH,CRITICAL deploy/{TARGETS[target][0]}"
    done = run(command, ROOT, os.environ, timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
