"""The Railway template instructions (shape checks only): Railway builds templates in its console, not from a file."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "deploy" / "railway"


def readme() -> str:
    return (FOLDER / "README.md").read_text()


def test_readme_gives_the_image_and_the_port():
    text = readme()
    assert re.search(r"ghcr\.io/scombat/s1d1-tantive:\d+\.\d+\.\d+-(small|tiny)-onnx-int8", text)
    assert "8788" in text


def test_readme_lists_the_variables():
    text = readme()
    for name in ("PORT", "S1D1_TIERS", "S1D1_FORMATS", "S1D1_DEADLINE_MS", "S1D1_API_KEY"):
        assert f"`{name}`" in text, name


def test_readme_names_the_health_path_and_the_restart_policy():
    text = readme()
    assert "`/ready`" in text
    assert "On failure" in text or "ON_FAILURE" in text


def test_api_key_is_generated_by_the_template_not_written_here():
    text = readme()
    assert "${{secret(" in text
    assert not re.search(r"S1D1_API_KEY[^\n]*=\s*[A-Za-z0-9]{16,}", text)


def test_no_config_as_code_file_for_an_image_deployment():
    # Railway's config as code has no image field: the README must say so instead of shipping a useless file
    assert not (FOLDER / "railway.json").exists()
    assert not (ROOT / "railway.json").exists()
    assert "config as code" in readme().lower()


def test_readme_states_the_button_and_what_is_still_to_do():
    text = readme()
    assert "https://railway.com/button.svg" in text
    assert "https://railway.com/deploy/<template-code>" in text
    assert "Status: needs a one-time setup" in text
    assert "not created yet" in text.lower()


def test_readme_states_the_plan_limits_and_the_date():
    text = readme()
    assert re.search(r"^- RAM: \d+ MiB", text, re.M)
    assert "2026-10-21" in text
