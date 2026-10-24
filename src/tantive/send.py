"""Opt-in sending of the captures to the community ingestion (docs/26): the local spool over the capture files and
the thread that posts them.

The capture files are the queue. A cursor (`.send-cursor`: file name and byte offset) marks what has been delivered;
it only moves when the caller commits it, so a failed delivery sends the same lines again. The cursor and the
instance id (`.instance-id`) live in the capture directory, where the retention purge (capture-*.jsonl only) never
looks. Only the lines the capture marked `"send": true` (written while sending was on) ever leave: any other line,
from before 0.8.0 or from a period with sending off, is passed over. The text of a prompt goes to the ingestion and
nowhere else: nothing here logs or prints a line's content, the token or the URL (a log line carries a status code, a
count or an error class name, nothing more).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tantive import __version__
from tantive.capture import NAME
from tantive.telemetry import log_event, metrics

INSTANCE_FILE = ".instance-id"
CURSOR_FILE = ".send-cursor"
MAX_BATCH_BYTES = 1_048_576
MAX_WAIT = 600.0  # seconds: the longest back-off after repeated failures
REJECTED = frozenset({400, 413})  # the ingestion will never take this batch: skip it

# v1 prediction key -> key of the captured line (route, pieces, truncated, token counts, latency and the service
# version stay local)
_PREDICTION = {
    "pred_tier": "tier",
    "pred_score": "score",
    "pred_task": "task",
    "pred_needs_tools": "needs_tools",
    "pred_confidence": "confidence",
}


def _replace_atomically(path: Path, text: str) -> None:
    """Write through a temporary file in the same directory, then rename over the target."""
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def instance_id(directory: Path) -> str:
    """The random identifier of this instance, created on first use and then stable (it carries no information)."""
    path = Path(directory) / INSTANCE_FILE
    try:
        current = path.read_text(encoding="utf-8").strip()
    except OSError:
        current = ""
    if current:
        return current
    current = str(uuid.uuid4())
    _replace_atomically(path, current + "\n")
    return current


def to_ingest(row: dict, instance: str) -> dict:
    """One captured line as a v1 ingestion line (keys in this order; a missing key is None, except `terms_version`,
    which a marked line always carries: KeyError without it). The `send` mark stays local."""
    return {
        "v": 1,
        "id": row.get("id"),
        "time": row.get("time"),
        "prompt": row.get("prompt"),
        "format": row.get("format"),
        "instance_id": instance,
        "variant": row.get("variant"),
        "model_revision": row.get("revision"),
        "terms_version": row["terms_version"],
        **{key: row.get(source) for key, source in _PREDICTION.items()},
    }


def encode(line: dict) -> bytes:
    """One ingestion line as it goes into an NDJSON body (UTF-8, no NaN or Infinity: ValueError instead).

    The spool sizes each line with this and the sender builds the body with it, so a batch is exactly what was sized."""
    return (json.dumps(line, ensure_ascii=False, allow_nan=False) + "\n").encode()


_PASS = object()  # a line not marked for sending: the cursor moves past it, nothing is counted


@dataclass(frozen=True)
class Cursor:
    """A position in the capture files: the file name and the byte offset of the next line to send."""

    file: str
    offset: int


def _key(name: str) -> tuple[str, int] | None:
    m = NAME.match(name)
    return (m.group(1), int(m.group(2) or 0)) if m else None


class Spool:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.full = False  # the last read stopped at `limit` or `max_bytes`, not at the end: more may follow
        self.passed = 0  # lines of the last read not marked for sending

    def _files(self) -> list[tuple[tuple[str, int], str]]:
        """The capture files, oldest first by (day, index): the plain name is index 0 and sorts before -001."""
        try:
            names = [p.name for p in self.dir.iterdir()]
        except OSError:
            return []
        return sorted((key, name) for name in names if (key := _key(name)))

    def _load(self) -> Cursor | None:
        try:
            data = json.loads((self.dir / CURSOR_FILE).read_text(encoding="utf-8"))
            return Cursor(str(data["file"]), int(data["offset"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None  # no cursor, or an unreadable one: start over (a repeat is better than a loss)

    def _position(self, files: list[tuple[tuple[str, int], str]]) -> tuple[int, int, Cursor | None]:
        """Where reading starts: the index in `files`, the offset in that file, and the saved cursor (None if none).

        The cursor's file, or after a purge the first existing file that comes after it (from its start). An offset
        beyond the file's size (the file was replaced or truncated) restarts that file at 0: a repeat is better than a
        loss, and the ingestion dedupes on id."""
        saved = self._load()
        if saved is None or (saved_key := _key(saved.file)) is None:
            return 0, 0, None
        start = next((i for i, (key, _) in enumerate(files) if key >= saved_key), len(files))
        offset = 0
        if start < len(files) and files[start][0] == saved_key:
            offset = max(saved.offset, 0)
            try:
                if offset > (self.dir / files[start][1]).stat().st_size:
                    offset = 0
            except OSError:  # purged meanwhile: the read finds out
                pass
        return start, offset, saved

    def pending(self) -> int:
        """Bytes of capture files after the cursor (marked or not, complete or not)."""
        files = self._files()
        start, offset, _ = self._position(files)
        total = 0
        for _, name in files[start:]:
            try:
                total += (self.dir / name).stat().st_size
            except OSError:
                pass
        return max(total - offset, 0)

    def commit(self, cursor: Cursor) -> None:
        _replace_atomically(self.dir / CURSOR_FILE, json.dumps({"file": cursor.file, "offset": cursor.offset}))

    def read(self, limit: int, max_bytes: int = 1_048_576) -> tuple[list[dict], Cursor, int]:
        """Up to `limit` v1 lines after the cursor (together at most `max_bytes` as NDJSON), the cursor after them,
        and the number of lines skipped.

        A line without `"send": true` (captured before 0.8.0 or while sending was off) is passed: never returned,
        never counted, the cursor moves past it. A marked line is skipped (never returned, never logged, counted)
        when it is not a JSON object, has no `terms_version`, holds NaN or Infinity, or when its ingestion encoding
        alone exceeds `max_bytes`; the returned cursor moves past it, even when no row comes back, so committing never
        gets stuck. A last line without its newline is still being written and waits. Afterwards `self.full` says
        whether the read stopped at `limit` or `max_bytes` rather than at the end of the files, and `self.passed`
        how many lines were passed.
        """
        self.full = False
        self.passed = 0
        files = self._files()
        start, offset, saved = self._position(files)
        if start >= len(files):
            return [], saved or Cursor("", 0), 0

        instance = instance_id(self.dir)
        rows: list[dict] = []
        skipped = used = 0
        cursor = Cursor(files[start][1], offset)
        for i in range(start, len(files)):
            name = files[i][1]
            last = i == len(files) - 1
            cursor = Cursor(name, offset)
            try:
                with (self.dir / name).open("rb") as f:
                    f.seek(offset)
                    for raw in f:
                        if not raw.endswith(b"\n"):  # partial line
                            if not last:  # nobody will complete it: the writer has moved on
                                skipped += 1
                                offset += len(raw)
                                cursor = Cursor(name, offset)
                            break
                        before = Cursor(name, offset)
                        parsed = self._parse(raw, instance, max_bytes)
                        if isinstance(parsed, tuple) and used + parsed[1] > max_bytes:
                            self.full = True
                            return rows, before, skipped  # this line opens the next batch
                        offset += len(raw)
                        cursor = Cursor(name, offset)
                        if parsed is _PASS:
                            self.passed += 1
                        elif parsed is None:
                            skipped += 1
                        else:
                            rows.append(parsed[0])
                            used += parsed[1]
                            if len(rows) >= limit:
                                self.full = True
                                return rows, cursor, skipped
            except FileNotFoundError:  # purged between the listing and the read
                pass
            offset = 0
            if not last:
                cursor = Cursor(files[i + 1][1], 0)
        return rows, cursor, skipped

    @staticmethod
    def _parse(raw: bytes, instance: str, max_bytes: int) -> tuple[dict, int] | object | None:
        """The v1 line and its size in an NDJSON batch, `_PASS` when the line is not marked for sending, or None
        when it cannot be sent at all."""
        try:
            row = json.loads(raw)
        except (ValueError, RecursionError):
            return None
        if not isinstance(row, dict):
            return None
        if row.get("send") is not True:
            return _PASS
        if row.get("terms_version") is None:
            return None
        out = to_ingest(row, instance)
        try:
            size = len(encode(out))
        except ValueError:  # NaN or Infinity: not JSON
            return None
        return (out, size) if size <= max_bytes else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is a failure: following it would send the token and the prompts to another address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def post_ndjson(url: str, token: str | None, body: bytes, timeout: float = 10.0) -> int:
    """POST one NDJSON batch: the HTTP status, or 0 when no answer came (network error, timeout, bad URL).

    Never raises. Redirects are not followed (their 3xx code comes back, which the sender treats as a failure)."""
    headers = {"Content-Type": "application/x-ndjson", "User-Agent": f"s1d1-tantive/{__version__}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with _opener.open(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as e:
        e.close()
        return int(e.code)
    except Exception:  # URLError, timeouts, OSError, a malformed URL: no answer
        return 0


class Sender:
    """One daemon thread (`s1d1-send`) that posts the spool batch by batch to the ingestion.

    The cursor moves on 2xx (sent) and on 400 or 413 (rejected: that batch will never be taken); any other answer,
    0 included, leaves it where it was, so the same lines go again after the wait. The wait is `interval` after a
    batch was sent or rejected; none at all when that batch was full (`batch` lines, or the 1 MiB cap), so a backlog
    drains batch after batch; after a failure it doubles each time, up to 600 s (or `interval` when that is longer),
    and goes back to `interval` at the first result that is not a failure. Nothing to send waits `interval`."""

    def __init__(
        self,
        directory: str,
        *,
        url: str,
        token: str | None,
        batch: int,
        interval: float,
        post: Callable[[str, str | None, bytes], int] = post_ndjson,
        sleep: Callable[[float], object] = time.sleep,
        start: bool = True,
    ) -> None:
        self.spool = Spool(Path(directory))
        self.instance = instance_id(self.spool.dir)
        self.url, self.token, self.batch, self.interval = url, token, batch, float(interval)
        self._post = post
        self._stop = threading.Event()
        # the real thread waits on the stop event, so close() interrupts the wait; an injected sleep (tests) is used
        # as given
        self._wait: Callable[[float], object] = self._stop.wait if sleep is time.sleep else sleep
        self._failing = False
        self._full = False  # the last batch sent or rejected was full: send the next one without waiting
        self._thread = threading.Thread(target=self._run, name="s1d1-send", daemon=True)
        if start:
            self.start()

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        """Stop the loop; a batch in flight finishes (or times out) first."""
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(15)

    def run_once(self) -> str:
        """Send one batch: "sent", "empty", "rejected" or "failed"."""
        self._full = False
        try:
            result = self._send_batch()
        finally:
            metrics().capture_send_pending(self.spool.pending())
        self._failing = result == "failed"  # an outage ends at the first result that is not a failure
        return result

    def _send_batch(self) -> str:
        rows, cursor, skipped = self.spool.read(self.batch, MAX_BATCH_BYTES)
        if not rows:
            if self.spool.passed or skipped:
                self.spool.commit(cursor)  # lines not marked for sending, unreadable or oversized: pass them
            if not skipped:
                return "empty"
            metrics().capture_send_rejected(skipped)
            log_event("capture_send_skipped", level=logging.WARNING, lines=skipped)
            return "rejected"
        body = b"".join(encode(r) for r in rows)
        status = self._post(self.url, self.token, body)
        if 200 <= status < 300 or status in REJECTED:
            self.spool.commit(cursor)
            self._full = self.spool.full
            if skipped:  # counted when the cursor passes them, so a retried batch does not count them again
                metrics().capture_send_rejected(skipped)
                log_event("capture_send_skipped", level=logging.WARNING, lines=skipped)
            if status in REJECTED:
                metrics().capture_send_rejected(len(rows))
                log_event("capture_send_rejected", level=logging.WARNING, status=status)
                return "rejected"
            metrics().capture_sent(len(rows))
            return "sent"
        metrics().capture_send_failed()
        if not self._failing:  # one line per outage
            log_event("capture_send_failed", level=logging.WARNING, status=status)
        return "failed"

    def _run(self) -> None:
        wait = self.interval
        while not self._stop.is_set():
            try:
                result = self.run_once()
            except Exception as e:  # a disk error, a bug: never the end of the thread, never the content
                metrics().capture_send_failed()
                if not self._failing:
                    log_event("capture_send_error", level=logging.WARNING, error=type(e).__name__)
                self._failing, self._full = True, False
                result = "failed"
            if result == "failed":
                wait = min(wait * 2, max(MAX_WAIT, self.interval))
            else:
                wait = self.interval
                if self._full:
                    continue  # a backlog: the next batch now (the loop checks the stop event first)
            if self._stop.is_set():
                return
            self._wait(wait)
