"""Verify a weights folder against the checksums the repository pins for a variant. Standard library only: it runs
in the image build (before the weights are copied in, and again inside the image) and the client can replay it.

    python -m tantive.verify /model /model/checksums.json

The checksums file is `{"repo", "revision", "files": {name: {"sha256"|"git_sha1", "size"}}}`: LFS files carry the
sha256 the Hub publishes, small files the git blob sha1 (`sha1(b"blob <size>\\0" + bytes)`).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

TOLERATED = frozenset({"REVISION", "checksums.json"})  # written by the build, not part of the Hub repo


def git_blob_sha1(path: str | os.PathLike[str]) -> str:
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def sha256_of(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(model_dir: str | os.PathLike[str], checksums_path: str | os.PathLike[str]) -> list[str]:
    """The problems found (`missing: name`, `mismatch: name`, `unexpected: name`); an empty list when all is well."""
    folder = Path(model_dir)
    checksums = json.loads(Path(checksums_path).read_text(encoding="utf-8"))
    expected: dict[str, dict] = checksums["files"]
    problems = []
    for name, spec in expected.items():
        path = folder / name
        if not path.is_file():
            problems.append(f"missing: {name}")
            continue
        if path.stat().st_size != spec["size"]:
            problems.append(f"mismatch: {name}")
            continue
        actual = sha256_of(path) if "sha256" in spec else git_blob_sha1(path)
        if actual != spec.get("sha256", spec.get("git_sha1")):
            problems.append(f"mismatch: {name}")
    present = {p.name for p in folder.iterdir() if p.is_file()}
    problems += [f"unexpected: {name}" for name in sorted(present - set(expected) - TOLERATED)]
    revision = folder / "REVISION"
    if revision.is_file() and revision.read_text(encoding="utf-8").strip() != checksums["revision"]:
        problems.append("mismatch: REVISION")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m tantive.verify MODEL_DIR CHECKSUMS_JSON", file=sys.stderr)
        return 2
    problems = verify(argv[0], argv[1])
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"ok: {argv[0]} matches {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
