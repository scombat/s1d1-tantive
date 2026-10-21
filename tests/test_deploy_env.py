"""Cross-target deployment debts: the optional OTLP variable, the Cloud Run key note, the Space licence link."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OTEL = "OTEL_EXPORTER_OTLP_ENDPOINT"


def test_otel_optional_in_every_manifest():
    """A one-click deploy must not prompt for a value nobody has: empty default, never required."""
    # Render: a literal empty value (no `sync: false`, which prompts; no generateValue)
    render = {e["key"]: e for e in yaml.safe_load((ROOT / "render.yaml").read_text())["services"][0]["envVars"]}
    assert render[OTEL]["value"] == "" and "sync" not in render[OTEL] and "generateValue" not in render[OTEL]
    # Heroku and Cloud Run buttons: required defaults to true, so it must say false, and carry no default
    for path in (ROOT / "app.json", ROOT / "deploy" / "gcp" / "app.json"):
        entry = json.loads(path.read_text())["env"][OTEL]
        assert entry["required"] is False and entry["description"], path
        assert "value" not in entry and "generator" not in entry, path
    # AWS: parameter with an empty default, the variable only added when it is given
    template = (ROOT / "deploy" / "aws" / "template.yaml").read_text()
    assert re.search(r"^  OtlpEndpoint:\n    Type: String\n    Default: \"\"", template, re.M)
    assert "HasOtlpEndpoint:" in template and "Name: OTEL_EXPORTER_OTLP_ENDPOINT" in template
    # Hugging Face and Railway have no machine-readable manifest: the guides list it as optional
    for guide in (ROOT / "deploy" / "huggingface" / "PUBLISH.md", ROOT / "deploy" / "railway" / "README.md"):
        lines = [line for line in guide.read_text().splitlines() if OTEL in line]
        assert lines and all("optional" in line.lower() for line in lines), guide


def test_cloud_run_readme_moves_the_key_to_secret_manager():
    text = (ROOT / "deploy" / "gcp" / "README.md").read_text()
    assert "plain environment variable" in text
    assert "gcloud secrets create" in text
    assert "--update-secrets S1D1_API_KEY=$SECRET:1" in text


def test_space_licence_link_is_a_file_of_the_space_itself():
    card = (ROOT / "deploy" / "huggingface" / "README.md").read_text()
    meta = yaml.safe_load(card.split("---")[1])
    # the repository is private for now, so a GitHub URL would 404 for readers; the Hub shows a LICENSE of the Space
    assert meta["license_link"] == "LICENSE"
    publish = (ROOT / "deploy" / "huggingface" / "PUBLISH.md").read_text()
    assert re.search(r"hf upload scombat/s1d1-tantive LICENSE LICENSE --type space", publish)
