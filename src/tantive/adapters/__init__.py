"""The wire formats (spec § 3.5): each module exposes `router(app) -> APIRouter` and registers it here under its
S1D1_FORMATS name. An adapter only translates request and response shapes; the decisions are the core's."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, FastAPI

AdapterFactory = Callable[[FastAPI], APIRouter]
ADAPTERS: dict[str, AdapterFactory] = {}


def register(name: str) -> Callable[[AdapterFactory], AdapterFactory]:
    def wrap(factory: AdapterFactory) -> AdapterFactory:
        ADAPTERS[name] = factory
        return factory

    return wrap


from tantive.adapters import chat, classify, decisions, predict, systemone  # noqa: E402, F401  (each registers itself on import)
