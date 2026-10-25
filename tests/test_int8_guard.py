"""tools/int8_guard.py: the verdict, the probe it reads, and the image workflow step that runs it under qemu."""

import json
import platform
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import int8_guard  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROBE = json.loads((ROOT / "tests" / "fixtures" / "int8_probe.json").read_text())


def test_identical_scores_pass():
    prompts = ["hi", "hello", "a", "b"]
    failures, numbers = int8_guard.verdict(prompts, [0.01, 0.02, 0.5, 0.8], [0.01, 0.02, 0.5, 0.8])
    assert failures == []
    assert numbers["agreement"] == 1.0


def test_saturated_scores_fail_on_every_rule():
    # the shape of the pre-0.8.1 small on AVX2 without VNNI: greetings pinned at 1.0, tiers scrambled
    prompts = ["hi", "bonjour", "a", "b", "c"]
    failures, numbers = int8_guard.verdict(prompts, [0.01, 0.04, 0.3, 0.5, 0.8], [1.0, 1.0, 0.1, 0.5, 0.8])
    assert numbers["agreement"] == 0.4
    assert numbers["pinned"] == 2
    assert any(f.startswith("tier agreement") for f in failures)
    assert any("trivial 'hi'" in f for f in failures)
    assert sum("pinned at 1.0" in f for f in failures) == 2


def test_a_trivial_prompt_above_trivial_fails_alone():
    failures, _ = int8_guard.verdict(["thanks", "a"], [0.14, 0.5], [0.16, 0.5])
    assert any("trivial 'thanks'" in f for f in failures)


def test_a_high_score_is_not_pinned_when_fp32_is_high_too():
    failures, numbers = int8_guard.verdict(["a"], [0.95], [1.0])
    assert numbers["pinned"] == 0 and failures == []


def test_the_probe_has_the_trivial_prompts_and_its_fp32_reference():
    texts = [p["text"] for p in PROBE["prompts"]]
    assert len(texts) >= 40 and len(set(texts)) == len(texts)
    assert set(int8_guard.TRIVIAL) <= set(texts)
    for p in PROBE["prompts"]:
        assert 0.01 <= p["fp32"] <= 1.0
        if p["text"] in int8_guard.TRIVIAL:
            assert p["fp32"] <= 0.15, p  # the reference itself passes the trivial rule
    assert len(PROBE["reference"]["sha256"]) == 64
    assert PROBE["reference"]["onnxruntime"]


def test_cpu_features_reads_cpuid_on_x86_and_nothing_elsewhere():
    features = int8_guard.cpu_features()
    if platform.machine().lower() in ("x86_64", "amd64"):
        assert set(features) == set(int8_guard.FEATURES)
        assert features["avx2"] is True  # every CI runner and every supported amd64 host has AVX2
    else:
        assert features is None


def test_image_workflow_runs_the_guard_under_qemu_haswell_on_the_tested_image():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "image.yml").read_text())
    on = workflow[True] if True in workflow else workflow["on"]
    for path in ("tools/int8_guard.py", "tests/fixtures/int8_probe.json", "checksums/**"):
        assert path in on["pull_request"]["paths"], path
    runs = [s.get("run", "") for s in workflow["jobs"]["image"]["steps"]]
    tests = next(i for i, r in enumerate(runs) if "pytest -m docker" in r)
    guard = next(i for i, r in enumerate(runs) if "int8_guard.py check" in r)
    assert tests < guard
    assert "qemu-user-static" in runs[guard - 1]
    assert "-cpu Haswell" in runs[guard] and "tantive:test-small" in runs[guard]
    assert (ROOT / "tests" / "test_image.py").read_text().count('IMAGE = "tantive:test-small"') == 1
