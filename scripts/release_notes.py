"""Print one version's changelog section for the public repository release."""

import argparse
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def extract_notes(changelog: str, version: str) -> str:
    heading = re.compile(r"^##\s+" + re.escape(version) + r"(?:\s+\([^\n]*\))?\s*$", re.MULTILINE)
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"No CHANGELOG section for version {version}")
    remaining = changelog[match.end() :]
    next_heading = re.search(r"^##\s+", remaining, re.MULTILINE)
    notes = remaining[: next_heading.start()] if next_heading else remaining
    return notes.strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    args = parser.parse_args()
    try:
        notes = extract_notes(CHANGELOG.read_text(encoding="utf-8"), args.version)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    print(notes, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
