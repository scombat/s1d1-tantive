import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = re.compile(r"(CVE-\d{4}-\d+)\s+# until (\d{4}-\d{2}-\d{2}): (.+)")


def entries() -> list[str]:
    lines = (ROOT / ".trivyignore").read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def test_trivyignore_exists_with_the_format_in_its_header():
    text = (ROOT / ".trivyignore").read_text()
    assert "# until YYYY-MM-DD:" in text


def test_trivyignore_entries_dated():
    for line in entries():
        m = ENTRY.fullmatch(line)
        assert m, f"malformed exception: {line!r} (expected 'CVE-XXXX-NNNN  # until YYYY-MM-DD: reason')"
        assert date.fromisoformat(m[2]) >= date.today(), f"{m[1]} exception expired on {m[2]}"
        assert len(m[3].strip()) >= 10, f"{m[1]}: give a real reason"
