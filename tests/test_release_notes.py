"""tools/release_notes.py: the release body is the CHANGELOG section of the version."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "release_notes.py"

CHANGELOG = """# Changelog

## 0.7.0 — 2026-11-01

- seven

## 0.6.0 — 2026-10-21

- six
- six bis

## Before 0.6.0 (private pre-releases)

- old
"""


def run(tmp_path, version, changelog=CHANGELOG):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(changelog)
    return subprocess.run([sys.executable, str(TOOL), version, str(path)], capture_output=True, text=True)


def test_extracts_the_section_without_its_title(tmp_path):
    result = run(tmp_path, "0.6.0")
    assert result.returncode == 0
    assert result.stdout == "- six\n- six bis\n"


def test_last_section_runs_to_the_next_title_only(tmp_path):
    assert run(tmp_path, "0.7.0").stdout == "- seven\n"


def test_accepts_the_tag_form(tmp_path):
    assert run(tmp_path, "v0.6.0").stdout == "- six\n- six bis\n"


def test_missing_version_fails(tmp_path):
    result = run(tmp_path, "9.9.9")
    assert result.returncode != 0
    assert "9.9.9" in result.stderr


def test_the_real_changelog_has_the_current_version():
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    args = [sys.executable, str(TOOL), version, str(ROOT / "CHANGELOG.md")]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip()
