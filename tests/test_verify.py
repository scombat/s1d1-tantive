import hashlib
import json

import pytest

from tantive.verify import git_blob_sha1, main, verify


@pytest.fixture
def tmp_dir(tmp_path):
    onnx = b"\x00onnx-bytes" * 100
    config = b'{"a": 1}\n'
    (tmp_path / "model_int8.onnx").write_bytes(onnx)
    (tmp_path / "config.json").write_bytes(config)
    checksums = {
        "repo": "scombat/x",
        "revision": "f" * 40,
        "files": {
            "model_int8.onnx": {"sha256": hashlib.sha256(onnx).hexdigest(), "size": len(onnx)},
            "config.json": {"git_sha1": git_blob_sha1(tmp_path / "config.json"), "size": len(config)},
        },
    }
    (tmp_path / "checksums.json").write_text(json.dumps(checksums))
    return tmp_path


def test_verify_ok(tmp_dir):
    assert verify(tmp_dir, tmp_dir / "checksums.json") == []


def test_verify_detects_tampered_file(tmp_dir):
    (tmp_dir / "model_int8.onnx").write_bytes(b"x")
    assert verify(tmp_dir, tmp_dir / "checksums.json") == ["mismatch: model_int8.onnx"]


def test_verify_detects_size_only_change(tmp_dir):
    # same prefix, one byte appended: the sha differs, and the size is reported with the name
    (tmp_dir / "config.json").write_bytes(b'{"a": 1}\n\n')
    assert verify(tmp_dir, tmp_dir / "checksums.json") == ["mismatch: config.json"]


def test_verify_detects_missing_and_unexpected(tmp_dir):
    (tmp_dir / "config.json").unlink()
    (tmp_dir / "extra.bin").write_bytes(b"e")
    assert set(verify(tmp_dir, tmp_dir / "checksums.json")) == {"missing: config.json", "unexpected: extra.bin"}


def test_verify_tolerates_revision_and_checksums_files(tmp_dir):
    (tmp_dir / "REVISION").write_text("f" * 40 + "\n")
    assert verify(tmp_dir, tmp_dir / "checksums.json") == []


def test_verify_rejects_a_wrong_revision_file(tmp_dir):
    (tmp_dir / "REVISION").write_text("0" * 40 + "\n")
    assert verify(tmp_dir, tmp_dir / "checksums.json") == ["mismatch: REVISION"]


def test_git_blob_sha1_matches_git(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"hello\n")
    assert git_blob_sha1(p) == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_verify_main_exit_codes(tmp_dir, capsys):
    args = [str(tmp_dir), str(tmp_dir / "checksums.json")]
    assert main(args) == 0
    assert "ok" in capsys.readouterr().out
    (tmp_dir / "model_int8.onnx").write_bytes(b"x")
    assert main(args) == 1
    assert "mismatch: model_int8.onnx" in capsys.readouterr().out


def test_verify_main_usage():
    assert main([]) == 2
