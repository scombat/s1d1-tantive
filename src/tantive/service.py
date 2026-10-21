"""Service routes, always mounted and never behind the API key: /health, /ready, /metrics, /info (spec § 3.5)."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from tantive import __version__
from tantive.config import Settings
from tantive.engine import Engine
from tantive.model import ModelInfo
from tantive.telemetry import SERVICE_NAME
from tantive.tiers import TierSet


def router(engine: Engine, settings: Settings, info: ModelInfo, native: TierSet) -> APIRouter:
    r = APIRouter(tags=["service"])

    @r.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @r.get("/ready")
    def ready() -> JSONResponse:
        return JSONResponse({"ready": engine.ready}, status_code=200 if engine.ready else 503)

    @r.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @r.get("/info")
    def service_info() -> dict:
        cost = engine.cost
        return {
            "service": SERVICE_NAME,
            "version": __version__,
            "variant": info.variant,
            "revision": info.revision,
            "onnx_file": info.onnx_file,
            "max_length": info.max_length,
            "score_center": info.score_center,
            "score_scale": info.score_scale,
            "sigma": info.sigma,
            "sigma_source": info.sigma_source,
            "formats": sorted(settings.formats),
            "tiers": native.as_dict(),
            "long_prompts": settings.long_prompts,
            "chunks": {
                "tokens": settings.chunk_tokens,
                "overlap": settings.chunk_overlap,
                "max": settings.chunk_max,
                "score": settings.chunk_score,
                "task": settings.chunk_task,
            },
            "deadline_ms": settings.deadline_ms,
            "max_queue": settings.max_queue,
            "threads": settings.threads,
            "inter_op_threads": settings.inter_op_threads,
            "capture": {
                "enabled": settings.capture_dir is not None,
                "dir": settings.capture_dir,
                "sample": settings.capture_sample if settings.capture_dir else None,
                "min_margin": settings.capture_min_margin,
                "retention_days": settings.capture_retention_days,
            },
            "cost": None
            if cost is None
            else {"fixed_ms": round(cost.fixed_ms, 3), "per_token_ms": round(cost.per_token_ms, 4)},
        }

    return r
