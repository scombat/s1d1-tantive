"""Optional JSONL capture of the decisions, to label the prompts again and retrain (docs/capture.md).

Off unless S1D1_CAPTURE_DIR is set. The request path only filters, samples and puts a dict on a bounded queue; one
dedicated thread appends and flushes the lines. A full queue loses the decision and counts it. The text goes to this
file and nowhere else (not to the logs, not to OpenTelemetry).
"""

from __future__ import annotations

import json
import logging
import queue
import random
import re
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tantive import __version__
from tantive.config import ConfigError
from tantive.model import ModelInfo, Outputs
from tantive.telemetry import log_event, metrics
from tantive.tiers import TierSet, confidence

NAME = re.compile(r"^capture-(\d{4}-\d{2}-\d{2})(?:-(\d{3}))?\.jsonl$")
_STOP = object()
_FLUSH = object()


class Capture:
    def __init__(
        self,
        directory: str,
        *,
        info: ModelInfo,
        native: TierSet,
        sample: float = 1.0,
        min_margin: float | None = None,
        max_bytes: int = 100 * 1024 * 1024,
        retention_days: int | None = None,
        queue_size: int = 10_000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: Callable[[], float] = random.random,
        start: bool = True,
        terms_version: str = "v1",
        send: bool = False,
    ) -> None:
        self.dir = Path(directory)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            probe = self.dir / ".capture-probe"
            probe.write_text("")
            probe.unlink()
        except OSError as e:
            raise ConfigError(f"S1D1_CAPTURE_DIR: {directory!r} is not writable ({e.strerror or e})") from None
        self.info, self.native = info, native
        self.sample, self.min_margin, self.max_bytes = sample, min_margin, max_bytes
        self.retention_days, self.clock, self.rng = retention_days, clock, rng
        self.terms_version = terms_version
        self.send = send  # mark each line `"send": true`: only marked lines ever leave (tantive.send)
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(target=self._run, name="s1d1-capture", daemon=True)
        self._file = None
        self._day, self._index, self._size = "", 0, 0
        self._purged_on = ""
        self._failing = False
        if start:
            self.start()

    def start(self) -> None:
        self._purge(self.clock())  # at start-up, before the first decision
        self._thread.start()

    # --- request path: no I/O -------------------------------------------------------------------------------------

    def offer(self, prompt: str, route: str, fmt: str, outputs: Outputs, tier: str | None, latency_ms: float) -> None:
        score = outputs.complexity_score
        if self.min_margin is not None and self._margin(score) > self.min_margin:
            return
        if not self.rng() < self.sample:
            return
        now = self.clock()
        probabilities = self.native.probabilities(score, self.info.sigma)
        row = {
            "time": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            "id": str(uuid.uuid4()),
            "prompt": prompt,
            "format": fmt,
            "route": route,
            "tier": tier,
            "score": score,
            "task": outputs.task,
            "needs_tools": outputs.needs_tools_probability,
            "confidence": confidence(probabilities),
            "pieces": outputs.pieces,
            "truncated": outputs.truncated,
            "prompt_tokens": outputs.prompt_tokens,
            "latency_ms": round(latency_ms, 1),
            "variant": self.info.variant,
            "revision": self.info.revision,
            "service_version": __version__,
            "terms_version": self.terms_version,
        }
        if self.send:
            row["send"] = True
        try:
            self._queue.put_nowait((now, row))
        except queue.Full:
            metrics().capture_dropped()

    def _margin(self, score: float) -> float:
        """Distance to the nearest inner bound of the native set (the last bound closes the scale)."""
        return min(abs(score - b) for b in self.native.bounds[:-1])

    # --- writer thread --------------------------------------------------------------------------------------------

    def flush(self) -> None:
        """Wait until everything offered so far is on disk."""
        done = threading.Event()
        self._queue.put((_FLUSH, done))
        done.wait(10)

    def close(self) -> None:
        if self._thread.is_alive():
            self._queue.put((_STOP, None))
            self._thread.join(10)
        elif not self._queue.empty():  # never started: write what was offered, then stop
            self._queue.put((_STOP, None))
            self._run()
        if self._file:
            self._file.close()
            self._file = None

    def _run(self) -> None:
        while True:
            when, item = self._queue.get()
            if when is _STOP:
                return
            if when is _FLUSH:
                item.set()
                continue
            try:
                self._write(when, item)
                self._failing = False
                metrics().capture_written()
            except OSError as e:
                metrics().capture_dropped()
                if not self._failing:  # one line per outage, never the text
                    log_event("capture_write_failed", level=logging.WARNING, error=str(e))
                self._failing = True

    def _write(self, when: datetime, row: dict) -> None:
        day = when.strftime("%Y-%m-%d")
        line = (json.dumps(row, ensure_ascii=False) + "\n").encode()
        if day != self._day:
            self._open_day(day)
            self._purge(when)
        elif self._size and self._size + len(line) > self.max_bytes:
            self._open(self._index + 1)
        self._file.write(line)
        self._file.flush()
        self._size += len(line)

    def _open_day(self, day: str) -> None:
        """Resume the last file of the day (a restart appends), or start it."""
        self._day = day
        indexes = [int(m.group(2) or 0) for p in self.dir.iterdir() if (m := NAME.match(p.name)) and m.group(1) == day]
        self._open(max(indexes, default=0))

    def _open(self, index: int) -> None:
        if self._file:
            self._file.close()
        self._index = index
        suffix = f"-{index:03d}" if index else ""
        path = self.dir / f"capture-{self._day}{suffix}.jsonl"
        self._file = path.open("ab")
        self._size = path.stat().st_size

    def _purge(self, now: datetime) -> None:
        """Delete the capture files older than the retention, at most once per day."""
        today = now.strftime("%Y-%m-%d")
        if self.retention_days is None or self._purged_on == today:
            return
        self._purged_on = today
        limit = (now - timedelta(days=self.retention_days)).strftime("%Y-%m-%d")
        for p in self.dir.iterdir():
            if (m := NAME.match(p.name)) and m.group(1) < limit:
                try:
                    p.unlink()
                except OSError as e:
                    log_event("capture_purge_failed", level=logging.WARNING, file=p.name, error=str(e))
