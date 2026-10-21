"""Settings read from the environment (spec § 4). Validated once at start-up; every error names its variable."""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

LONG_PROMPTS: tuple[str, ...] = ("head", "tail", "head_tail", "chunks")
CHUNK_SCORES: tuple[str, ...] = ("max", "mean", "median", "first", "length_weighted")
CHUNK_TASKS: tuple[str, ...] = ("sum_logits", "vote")
FORMATS: tuple[str, ...] = ("systemone", "decisions", "chat", "classify", "predict")
DEFAULT_FORMATS = "systemone,decisions,classify"
CGROUP_CPU_MAX = "/sys/fs/cgroup/cpu.max"


class ConfigError(ValueError):
    """An environment variable has an invalid value; the message names it."""


def cpu_count_from_cgroup() -> int:
    """CPUs granted to this container by cgroup v2 (`quota period` in cpu.max, rounded up), else the host count."""
    try:
        with open(CGROUP_CPU_MAX, encoding="utf-8") as f:
            quota, period = f.read().split()
        if quota != "max":
            return max(1, math.ceil(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    return os.cpu_count() or 1


@dataclass(frozen=True)
class Settings:
    formats: frozenset[str]
    model_dir: str
    threads: int
    inter_op_threads: int
    tiers: str | None
    default_sigma: float  # sigma when variant.json carries none
    long_prompts: Literal["head", "tail", "head_tail", "chunks"]
    chunk_tokens: int
    chunk_overlap: int
    chunk_max: int
    chunk_score: Literal["max", "mean", "median", "first", "length_weighted"]
    chunk_task: Literal["sum_logits", "vote"]
    deadline_ms: int
    max_queue: int
    api_key: str | None
    log_prompts: bool
    host: str
    port: int
    root_path: str
    otlp_endpoint: str | None
    capture_dir: str | None = None
    capture_sample: float = 1.0
    capture_min_margin: float | None = None
    capture_max_mb: int = 100
    capture_retention_days: int | None = None
    capture_queue: int = 10000

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        formats = frozenset(f.strip() for f in env.get("S1D1_FORMATS", DEFAULT_FORMATS).split(",") if f.strip())
        unknown = sorted(formats - set(FORMATS))
        if unknown or not formats:
            raise ConfigError(f"S1D1_FORMATS: unknown format(s) {unknown}; choose among {', '.join(FORMATS)}")
        chunk_tokens = _int(env, "S1D1_CHUNK_TOKENS", default=1024, minimum=128, maximum=1024)
        chunk_overlap = _int(env, "S1D1_CHUNK_OVERLAP", default=64, minimum=0, maximum=chunk_tokens - 3)
        return cls(
            formats=formats,
            model_dir=env.get("S1D1_MODEL_DIR", "/model"),
            threads=_int(env, "S1D1_THREADS", default=cpu_count_from_cgroup, minimum=1),
            inter_op_threads=_int(env, "S1D1_INTER_OP_THREADS", default=1, minimum=1),
            tiers=env.get("S1D1_TIERS") or None,
            default_sigma=_positive_float(env, "S1D1_DEFAULT_SIGMA", default=0.10),
            long_prompts=_choice(env, "S1D1_LONG_PROMPTS", LONG_PROMPTS, default="head_tail"),
            chunk_tokens=chunk_tokens,
            chunk_overlap=chunk_overlap,
            chunk_max=_int(env, "S1D1_CHUNK_MAX", default=8, minimum=1, maximum=32),
            chunk_score=_choice(env, "S1D1_CHUNK_SCORE", CHUNK_SCORES, default="max"),
            chunk_task=_choice(env, "S1D1_CHUNK_TASK", CHUNK_TASKS, default="sum_logits"),
            deadline_ms=_int(env, "S1D1_DEADLINE_MS", default=800, minimum=1),
            max_queue=_int(env, "S1D1_MAX_QUEUE", default=32, minimum=1),
            api_key=env.get("S1D1_API_KEY") or None,
            log_prompts=env.get("S1D1_LOG_PROMPTS", "0") == "1",
            host=env.get("S1D1_HOST", "0.0.0.0"),
            port=_int(env, "S1D1_PORT", default=8788, minimum=1),
            root_path=env.get("S1D1_ROOT_PATH", ""),
            otlp_endpoint=env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            capture_dir=env.get("S1D1_CAPTURE_DIR") or None,
            capture_sample=_unit_float(env, "S1D1_CAPTURE_SAMPLE", default=1.0),
            capture_min_margin=_optional_float(env, "S1D1_CAPTURE_MIN_MARGIN"),
            capture_max_mb=_int(env, "S1D1_CAPTURE_MAX_MB", default=100, minimum=1),
            capture_retention_days=_optional_int(env, "S1D1_CAPTURE_RETENTION_DAYS", minimum=1),
            capture_queue=_int(env, "S1D1_CAPTURE_QUEUE", default=10000, minimum=1),
        )


def _choice(env: Mapping[str, str], name: str, allowed: tuple[str, ...], *, default: str) -> str:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip()
    if value not in allowed:
        raise ConfigError(f"{name}: {value!r} is not one of {', '.join(allowed)}")
    return value


def _int(
    env: Mapping[str, str], name: str, *, default: int | Callable[[], int], minimum: int, maximum: int | None = None
) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default() if callable(default) else default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name}: {raw!r} is not an integer") from None
    if value < minimum:
        raise ConfigError(f"{name}: {value} is below the minimum {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name}: {value} is above the maximum {maximum}")
    return value


def _positive_float(env: Mapping[str, str], name: str, *, default: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name}: {raw!r} is not a number") from None
    if not value > 0:
        raise ConfigError(f"{name}: {value} must be greater than 0")
    return value


def _optional_int(env: Mapping[str, str], name: str, *, minimum: int) -> int | None:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    return _int(env, name, default=0, minimum=minimum)


def _optional_float(env: Mapping[str, str], name: str) -> float | None:
    """A number of at least 0, or None when the variable is empty."""
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    value = _float(raw, name)
    if value < 0:
        raise ConfigError(f"{name}: {value} is below the minimum 0")
    return value


def _unit_float(env: Mapping[str, str], name: str, *, default: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    value = _float(raw, name)
    if not 0 <= value <= 1:
        raise ConfigError(f"{name}: {value} must be between 0 and 1")
    return value


def _float(raw: str, name: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name}: {raw!r} is not a number") from None
    if math.isnan(value):
        raise ConfigError(f"{name}: {raw!r} is not a number")
    return value
