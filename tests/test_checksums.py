import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from checksums import VARIANTS, checksums_of  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Lfs:
    sha256: str


@dataclass
class Sib:
    rfilename: str
    size: int
    blob_id: str | None = None
    lfs: Lfs | None = None


@dataclass
class FakeInfo:
    sha: str
    siblings: list = field(default_factory=list)


def test_checksums_of_fake_info():
    info = FakeInfo(
        sha="a" * 40,
        siblings=[
            Sib("model_int8.onnx", 10, lfs=Lfs("b" * 64)),
            Sib("config.json", 5, blob_id="c" * 40),
            Sib("README.md", 1, blob_id="d" * 40),
            Sib(".gitattributes", 1, blob_id="e" * 40),
        ],
    )
    c = checksums_of(info, repo="scombat/x")
    assert c["repo"] == "scombat/x"
    assert c["revision"] == "a" * 40
    assert c["files"]["model_int8.onnx"] == {"sha256": "b" * 64, "size": 10}
    assert c["files"]["config.json"] == {"git_sha1": "c" * 40, "size": 5}
    assert "README.md" not in c["files"]
    assert ".gitattributes" not in c["files"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["fetched"])


def test_variants_are_the_four_int8():
    assert VARIANTS == ("full-onnx-int8", "compact-onnx-int8", "small-onnx-int8", "tiny-onnx-int8")


def test_checksums_list_optional_vocab_map():
    small = json.loads((ROOT / "checksums" / "small-onnx-int8.json").read_text())
    full = json.loads((ROOT / "checksums" / "full-onnx-int8.json").read_text())
    assert "vocab_map.npy" in small["files"]
    assert "vocab_map.npy" not in full["files"]


def test_four_checksum_files_pin_a_commit():
    for v in VARIANTS:
        c = json.loads((ROOT / "checksums" / f"{v}.json").read_text())
        assert c["repo"] == f"scombat/s1d1-router-6src-{v}"
        assert re.fullmatch(r"[0-9a-f]{40}", c["revision"])
        assert re.fullmatch(r"[0-9a-f]{64}", c["files"]["model_int8.onnx"]["sha256"])
        for name in ("tokenizer.json", "tokenizer_config.json", "config.json", "variant.json", "LICENSE", "NOTICE"):
            assert name in c["files"], (v, name)
        for name in ("README.md", "eval.json", "footprint.json", ".gitattributes"):
            assert name not in c["files"], (v, name)
