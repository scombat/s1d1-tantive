"""Refresh checksums/<variant>.json from the Hub's file metadata (dev tool; needs huggingface_hub and the network).

    uv run python tools/checksums.py [--variant NAME]...

Each file pins the Hub commit (`revision`) and, per shipped file, the sha256 the Hub publishes for LFS files or the
git blob sha1 for the others, with the size. The build downloads at that commit and verifies with tantive.verify.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

VARIANTS = ("full-onnx-int8", "compact-onnx-int8", "small-onnx-int8", "tiny-onnx-int8")
REPO_PREFIX = "scombat/s1d1-router-6src-"
NOT_SHIPPED = frozenset({"README.md", "eval.json", "footprint.json", ".gitattributes"})
CHECKSUMS_DIR = Path(__file__).resolve().parents[1] / "checksums"


def checksums_of(info, repo: str) -> dict:
    """The checksums document for a `model_info(..., files_metadata=True)` result."""
    files = {}
    for sibling in sorted(info.siblings, key=lambda s: s.rfilename):
        name = sibling.rfilename
        if name in NOT_SHIPPED:
            continue
        if sibling.lfs is not None:
            files[name] = {"sha256": sibling.lfs.sha256, "size": sibling.size}
        else:
            files[name] = {"git_sha1": sibling.blob_id, "size": sibling.size}
    return {
        "repo": repo,
        "revision": info.sha,
        "fetched": dt.date.today().isoformat(),
        "files": files,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--variant", action="append", choices=VARIANTS, help="default: all four")
    args = parser.parse_args(argv)
    from huggingface_hub import HfApi

    api = HfApi()
    for variant in args.variant or VARIANTS:
        repo = REPO_PREFIX + variant
        info = api.model_info(repo, files_metadata=True)
        document = checksums_of(info, repo)
        path = CHECKSUMS_DIR / f"{variant}.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        print(f"{path.name}: {document['revision'][:7]}, {len(document['files'])} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
