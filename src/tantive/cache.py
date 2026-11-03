"""The in-memory cache of model outputs (docs/40 § 6): off by default, one time to live, first in first out.

The key is SHA-256(configuration fingerprint ‖ extracted text), 32 bytes; the value is the model's Outputs, never a
tier (tiers are computed again on every request). Neither the text nor the key is ever logged or exported: the
digest of a short prompt can be found back by dictionary.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable

from tantive import __version__
from tantive.longprompt import LongPromptPolicy
from tantive.model import ModelInfo, Outputs
from tantive.telemetry import metrics


def fingerprint(info: ModelInfo, policy: LongPromptPolicy) -> bytes:
    """What the outputs of a text depend on besides the text: the service version, the weights, the policy and the
    effective limit, and the chunks settings under `chunks` only. Computed once, at start-up."""
    parts: dict[str, object] = {
        "version": __version__,
        "variant": info.variant,
        "revision": info.revision,
        "onnx_file": info.onnx_file,
        "policy": policy.name,
        "limit": policy.limit,
    }
    if policy.name == "chunks":
        parts |= {
            "chunk_tokens": policy.chunk_tokens,
            "overlap": policy.overlap,
            "chunk_max": policy.chunk_max,
            "score": policy.score,
            "task": policy.task,
        }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).digest()


def cache_key(fingerprint: bytes, text: str) -> bytes:
    """The text as extracted, before the cut and without normalisation."""
    digest = hashlib.sha256(fingerprint)
    digest.update(text.encode("utf-8"))
    return digest.digest()


class Cache:
    """Outputs by key for `ttl_s` seconds, at most `max_entries`. An access does not extend an entry, so insertion
    order is expiry order: expired entries leave lazily on a read and from the head on an insert, the oldest one
    leaves first at capacity, all in O(1)."""

    def __init__(self, ttl_s: int, max_entries: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[bytes, tuple[float, Outputs]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: bytes) -> Outputs | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires, outputs = entry
        if self._clock() >= expires:
            del self._entries[key]
            self._evicted("expired")
            return None
        return outputs

    def put(self, key: bytes, outputs: Outputs) -> None:
        now = self._clock()
        self._entries.pop(key, None)
        while self._entries and next(iter(self._entries.values()))[0] <= now:
            self._entries.popitem(last=False)
            self._evicted("expired")
        while len(self._entries) >= self.max_entries:
            self._entries.popitem(last=False)
            self._evicted("capacity")
        self._entries[key] = (now + self.ttl_s, outputs)
        metrics().cache_entries(len(self._entries))

    def _evicted(self, reason: str) -> None:
        metrics().cache_evicted(reason)
        metrics().cache_entries(len(self._entries))
