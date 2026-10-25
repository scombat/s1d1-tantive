"""The int8 saturation guard: score a probe set with a served variant and compare with the fp32 reference scores.

    python -I tools/int8_guard.py check MODEL_DIR          # CI (image.yml): the small image under qemu -cpu Haswell
    python -I tools/int8_guard.py reference FP32_DIR       # rewrites the fp32 scores of tests/fixtures/int8_probe.json

Why: the int8 weights are U8S8 (quantize_dynamic, QInt8). On x86 without VNNI, ONNX Runtime multiplies with an AVX2
instruction that adds two u8 x s8 products into a saturated int16; weights on 8 bits overflowed it and the small
variant scored "hi" 1.0 (tier agreement with fp32 49 %). The weights since 0.8.1 are quantized with reduce_range
(7-bit weights), which leaves room for the sum. A CPU with VNNI or AMX never takes that path, so `check` reads the
CPU features the process sees (CPUID: under qemu-x86_64 it is the emulated CPU, not the host) and refuses to pass on
one that has them: the result would say nothing about the CPUs that matter.

`check` fails when the tier agreement with fp32 (training tiers) is below 85 %, when a trivial prompt scores above
the TRIVIAL bound, or when a score is pinned at 1.0 where fp32 is below 0.9. Scoring is tantive's own (OnnxModel.run,
one prompt per call as a request is), so the guard tests what is served.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import mmap
import platform
import sys
from pathlib import Path

PROBE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "int8_probe.json"
MIN_AGREEMENT = 0.85
TRIVIAL = ("hi", "hello", "thanks", "bonjour")
PINNED, PINNED_FP32_BELOW = 1.0, 0.9
# CPUID bits (Intel SDM vol. 2A, CPUID): (leaf, subleaf, register index eax=0 ebx=1 ecx=2 edx=3, bit)
FEATURES = {
    "fma": (1, 0, 2, 12),
    "osxsave": (1, 0, 2, 27),
    "avx": (1, 0, 2, 28),
    "avx2": (7, 0, 1, 5),
    "avx512f": (7, 0, 1, 16),
    "avx512bw": (7, 0, 1, 30),
    "avx512_vnni": (7, 0, 2, 11),
    "amx_int8": (7, 0, 3, 25),
    "avx_vnni": (7, 1, 0, 4),
    "avx_vnni_int8": (7, 1, 3, 4),
}
NO_SATURATION_PATH = ("avx512_vnni", "avx_vnni", "avx_vnni_int8", "amx_int8")  # int8 dot products without int16
# x86-64 SysV: cpuid(leaf=edi, subleaf=esi, out=rdx) writes eax, ebx, ecx, edx to out[0..3]; rbx is callee-saved
CPUID_CODE = bytes.fromhex("53 89f8 89f1 4989d0 0fa2 418900 41895804 41894808 4189500c 5b c3")


def cpu_features() -> dict[str, bool] | None:
    """The CPUID feature bits this process sees, None off x86-64. CPUID is executed, not read from /proc/cpuinfo,
    which qemu-user passes through from the host."""
    if platform.machine().lower() not in ("x86_64", "amd64"):
        return None
    page = mmap.mmap(-1, mmap.PAGESIZE, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
    page.write(CPUID_CODE)
    out_type = ctypes.c_uint32 * 4
    function = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(out_type))
    cpuid = function(ctypes.addressof(ctypes.c_char.from_buffer(page)))

    def registers(leaf: int, subleaf: int) -> tuple[int, ...]:
        out = out_type()
        cpuid(leaf, subleaf, out)
        return tuple(out)

    max_leaf = registers(0, 0)[0]
    max_subleaf = registers(7, 0)[0] if max_leaf >= 7 else -1
    features = {}
    for name, (leaf, subleaf, register, bit) in FEATURES.items():
        present = leaf <= max_leaf and (leaf != 7 or subleaf <= max_subleaf)
        features[name] = present and bool(registers(leaf, subleaf)[register] >> bit & 1)
    return features


def host_flags() -> str:
    """The host's /proc/cpuinfo flags of interest (for the log; under qemu-user they are not the effective ones)."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            line = next(x for x in f if x.startswith("flags"))
    except (OSError, StopIteration):
        return "unavailable"
    flags = line.split(":", 1)[1].split()
    return " ".join(f for f in flags if f in {"avx2", "fma", "avx512f", "avx512_vnni", "avx_vnni", "amx_int8"})


def scores_of(model_dir: str, prompts: list[str]) -> list[float]:
    from tantive.model import OnnxModel

    model = OnnxModel(model_dir, threads=2, inter_op_threads=1)
    return [model.run([p])[0].complexity_score for p in prompts]


def verdict(prompts: list[str], fp32: list[float], int8: list[float]) -> tuple[list[str], dict]:
    """The failures (empty when the variant passes) and the numbers they are drawn from."""
    from tantive.tiers import TRAINING_SET

    trivial_bound = TRAINING_SET.bounds[0]
    agree = [TRAINING_SET.tier_for(a) == TRAINING_SET.tier_for(b) for a, b in zip(fp32, int8, strict=True)]
    agreement = sum(agree) / len(agree)
    trivial = {p: s for p, s in zip(prompts, int8, strict=True) if p in TRIVIAL}
    pinned = [p for p, a, b in zip(prompts, fp32, int8, strict=True) if b >= PINNED and a < PINNED_FP32_BELOW]
    failures = []
    if agreement < MIN_AGREEMENT:
        failures.append(f"tier agreement {agreement:.1%} < {MIN_AGREEMENT:.0%}")
    failures += [f"trivial {p!r} scores {s:.3f} > {trivial_bound}" for p, s in trivial.items() if s > trivial_bound]
    failures += [f"{p[:40]!r} pinned at 1.0 (fp32 {a:.3f})" for p, a in zip(prompts, fp32, strict=True) if p in pinned]
    gaps = [abs(a - b) for a, b in zip(fp32, int8, strict=True)]
    numbers = {
        "agreement": agreement,
        "trivial": trivial,
        "pinned": len(pinned),
        "mean_gap": sum(gaps) / len(gaps),
        "max_gap": max(gaps),
    }
    return failures, numbers


def check(model_dir: str, any_cpu: bool) -> int:
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    features = cpu_features()
    print(f"machine {platform.machine()}, host /proc/cpuinfo flags: {host_flags()}")
    print("CPUID (effective): " + (" ".join(f"{k}={int(v)}" for k, v in features.items()) if features else "not x86"))
    unfit = features is None or any(features[f] for f in NO_SATURATION_PATH)
    if unfit and not any_cpu:
        print("refused: this CPU is not x86-64 without VNNI/AMX, the saturation path cannot be taken here; run under")
        print("  qemu-x86_64 -cpu Haswell (or pass --any-cpu to see the numbers anyway)")
        return 2
    prompts = [p["text"] for p in probe["prompts"]]
    fp32 = [p["fp32"] for p in probe["prompts"]]
    int8 = scores_of(model_dir, prompts)
    for p, a, b in zip(prompts, fp32, int8, strict=True):
        print(f"  fp32 {a:.3f}  int8 {b:.3f}  {p[:70]}")
    failures, n = verdict(prompts, fp32, int8)
    trivial = " ".join(f"{p}={s:.3f}" for p, s in n["trivial"].items())
    print(
        f"{len(prompts)} prompts: tier agreement {n['agreement']:.1%}, gap mean {n['mean_gap']:.4f} max "
        f"{n['max_gap']:.3f}, pinned at 1.0 {n['pinned']}, trivial {trivial}"
    )
    for failure in failures:
        print(f"FAIL: {failure}")
    print("int8 guard: " + ("FAILED" if failures else "passed"))
    return 1 if failures else 0


def reference(fp32_dir: str, source: str) -> int:
    import onnxruntime

    from tantive.verify import sha256_of

    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    prompts = [p if isinstance(p, str) else p["text"] for p in probe["prompts"]]
    variant = json.loads((Path(fp32_dir) / "variant.json").read_text(encoding="utf-8"))
    scores = scores_of(fp32_dir, prompts)
    document = {
        "about": "Probe of tools/int8_guard.py: prompts written for this test (none from a dataset), fp32 scores "
        "of the small variant computed by `int8_guard.py reference` with tantive's OnnxModel.",
        "reference": {
            "source": source,
            "file": variant.get("file", "model.onnx"),
            "sha256": sha256_of(Path(fp32_dir) / variant.get("file", "model.onnx")),
            "onnxruntime": onnxruntime.__version__,
            "machine": platform.machine(),
            "date": dt.date.today().isoformat(),
        },
        "prompts": [{"text": p, "fp32": s} for p, s in zip(prompts, scores, strict=True)],
    }
    PROBE.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{PROBE.name}: {len(prompts)} fp32 scores from {fp32_dir}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="score MODEL_DIR (int8) and compare with the fp32 scores of the probe")
    c.add_argument("model_dir")
    c.add_argument(
        "--any-cpu", action="store_true", help="score anyway on a CPU with VNNI/AMX or off x86 (local diagnosis)"
    )
    r = sub.add_parser("reference", help="score FP32_DIR and write the probe's fp32 scores")
    r.add_argument("fp32_dir")
    r.add_argument("--source", required=True, help="where the fp32 export comes from, recorded in the probe")
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args.model_dir, args.any_cpu)
    return reference(args.fp32_dir, args.source)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
