"""Print the CHANGELOG.md section of a version, the body of its GitHub release.

    python tools/release_notes.py <version|vVERSION> [CHANGELOG.md]

The section runs from the line `## <version> —` to the next `## ` title, the title itself excluded.
"""

from __future__ import annotations

import sys
from pathlib import Path


def section(changelog: str, version: str) -> str | None:
    body: list[str] = []
    inside = False
    for line in changelog.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith(f"## {version} —")
            continue
        if inside:
            body.append(line)
    if not inside:
        return None
    text = "\n".join(body).strip("\n")
    return text + "\n" if text else None


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 3:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[1].removeprefix("v")
    path = Path(argv[2]) if len(argv) == 3 else Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    notes = section(path.read_text(), version)
    if notes is None:
        print(f"no '## {version} —' section in {path}", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
