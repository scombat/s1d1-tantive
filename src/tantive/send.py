"""Opt-in sending of the captures to the community ingestion (docs/26): the local spool over the capture files.

The capture files are the queue. A cursor (`.send-cursor`: file name and byte offset) marks what has been delivered;
it only moves when the caller commits it, so a failed delivery sends the same lines again. The cursor and the
instance id (`.instance-id`) live in the capture directory, where the retention purge (capture-*.jsonl only) never
looks. The text of a prompt goes to the ingestion and nowhere else: nothing here logs or prints a line's content.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from tantive.capture import NAME

INSTANCE_FILE = ".instance-id"
CURSOR_FILE = ".send-cursor"

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
    """One captured line as a v1 ingestion line (keys in this order; a missing key is None, terms default to v1)."""
    return {
        "v": 1,
        "id": row.get("id"),
        "time": row.get("time"),
        "prompt": row.get("prompt"),
        "format": row.get("format"),
        "instance_id": instance,
        "variant": row.get("variant"),
        "model_revision": row.get("revision"),
        "terms_version": row.get("terms_version", "v1"),
        **{key: row.get(source) for key, source in _PREDICTION.items()},
    }


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

    def commit(self, cursor: Cursor) -> None:
        _replace_atomically(self.dir / CURSOR_FILE, json.dumps({"file": cursor.file, "offset": cursor.offset}))

    def read(self, limit: int, max_bytes: int = 1_048_576) -> tuple[list[dict], Cursor, int]:
        """Up to `limit` v1 lines after the cursor, the cursor after them, and the number of lines skipped.

        A line is skipped (never returned, never logged) when it is not a JSON object or when its ingestion encoding
        exceeds `max_bytes`; the returned cursor moves past it, even when no row comes back, so committing never
        gets stuck. A last line without its newline is still being written and waits.
        """
        files = self._files()
        saved = self._load()
        start, offset = 0, 0
        if saved is not None and (saved_key := _key(saved.file)) is not None:
            # the cursor's file, or after a purge the first existing file that comes after it (from its start)
            start = next((i for i, (key, _) in enumerate(files) if key >= saved_key), len(files))
            if start < len(files) and files[start][0] == saved_key:
                offset = max(saved.offset, 0)
        else:
            saved = None
        if start >= len(files):
            return [], saved or Cursor("", 0), 0

        instance = instance_id(self.dir)
        rows: list[dict] = []
        skipped = 0
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
                        offset += len(raw)
                        cursor = Cursor(name, offset)
                        parsed = self._parse(raw, instance, max_bytes)
                        if parsed is None:
                            skipped += 1
                        else:
                            rows.append(parsed)
                            if len(rows) >= limit:
                                return rows, cursor, skipped
            except FileNotFoundError:  # purged between the listing and the read
                pass
            offset = 0
            if not last:
                cursor = Cursor(files[i + 1][1], 0)
        return rows, cursor, skipped

    @staticmethod
    def _parse(raw: bytes, instance: str, max_bytes: int) -> dict | None:
        try:
            row = json.loads(raw)
        except (ValueError, RecursionError):
            return None
        if not isinstance(row, dict):
            return None
        out = to_ingest(row, instance)
        if len(json.dumps(out, ensure_ascii=False).encode()) + 1 > max_bytes:
            return None
        return out
