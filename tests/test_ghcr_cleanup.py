import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "ghcr_cleanup.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None or shutil.which("bash") is None, reason="needs jq and bash")

STUB = """#!/usr/bin/env bash
# fake gh: serves FAKE_GH_DIR/<package>.json, logs every DELETE to FAKE_GH_DIR/calls.log
if [ "$1" = api ] && [ "$2" = -X ] && [ "$3" = DELETE ]; then
  echo "$4" >> "$FAKE_GH_DIR/calls.log"
  exit 0
fi
if [ "$1" = api ] && [ "$2" = --paginate ]; then
  pkg="$(echo "$3" | sed -E 's#.*/container/([^/]+)/versions#\\1#')"
  cat "$FAKE_GH_DIR/$pkg.json"
  exit 0
fi
echo "unexpected gh call: $*" >&2
exit 1
"""


def version(vid, created, *tags):
    return {"id": vid, "created_at": created, "metadata": {"container": {"tags": list(tags)}}}


MAIN = [
    version(1, "2026-10-10T10:00:00Z", "0.4.0-small-onnx-int8", "0.4.0"),
    version(2, "2026-10-10T09:59:00Z"),  # untagged, child of 0.4.0
    version(3, "2026-10-18T10:00:00Z", "0.5.0-small-onnx-int8"),
    version(4, "2026-10-18T09:58:00Z"),  # untagged, child of the kept 0.5.0
    version(5, "2026-10-21T10:00:00Z", "0.5.0-tiny-onnx-int8", "latest"),
    version(6, "2026-10-21T09:59:00Z", "sha-abc"),  # other tag: stays
]
BASE = [
    version(10, "2026-10-09T10:00:00Z", "0.4.0"),
    version(11, "2026-10-18T09:00:00Z"),  # untagged, within the 24 h margin of the kept images
]


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(STUB)
    gh.chmod(0o755)
    data = tmp_path / "data"
    data.mkdir()
    (data / "s1d1-tantive.json").write_text(json.dumps(MAIN))
    (data / "s1d1-tantive-base.json").write_text(json.dumps(BASE))
    return {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_GH_DIR": str(data),
    }


def run(env, *args, stdin=""):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, input=stdin, capture_output=True, text=True)


def calls(env):
    log = Path(env["FAKE_GH_DIR"]) / "calls.log"
    return log.read_text().split() if log.exists() else []


def test_listing_before_any_deletion_and_abort_on_no(env):
    r = run(env, "--keep", "0.5.0", stdin="no\n")
    assert r.returncode == 1
    assert "delete  1 " in r.stdout and "kept version" in r.stdout
    assert "Type \"yes\"" in r.stdout
    assert calls(env) == []


def test_keep_version_is_never_deleted(env):
    r = run(env, "--keep", "0.5.0", "--yes")
    assert r.returncode == 0, r.stderr
    deleted = {c.rsplit("/", 1)[1] for c in calls(env)}
    # 0.4.0 images and the untagged child older than the kept images (minus 24 h)
    assert deleted == {"1", "2", "10"}
    assert not deleted & {"3", "4", "5", "6", "11"}
    assert all(c.startswith("/user/packages/container/") for c in calls(env))


def test_without_keep_deletes_every_prerelease_and_old_untagged(env):
    r = run(env, "--yes")
    assert r.returncode == 0, r.stderr
    deleted = {c.rsplit("/", 1)[1] for c in calls(env)}
    # version 5 carries "latest" but also a 0.* tag; "sha-abc" (6) stays and sets the untagged cutoff
    assert deleted == {"1", "2", "3", "4", "5", "10", "11"}


def test_refuses_an_unknown_keep_version(env):
    r = run(env, "--keep", "9.9.9", "--yes")
    assert r.returncode == 2
    assert "refusing" in r.stderr
    assert calls(env) == []


def test_unknown_argument(env):
    assert run(env, "--nope").returncode == 2
