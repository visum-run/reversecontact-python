import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
extract_notes = runpy.run_path(str(ROOT / "scripts/release_notes.py"))["extract_notes"]


@pytest.mark.parametrize("suffix", ["", " (unreleased)", " (2026-10-29)"])
def test_extracts_only_requested_version(suffix):
    changelog = (
        f"# Changes\n\n## 0.2.0{suffix}\n\n- New feature.\n\n"
        "### Details\n\nMore.\n\n## 0.1.0\n\n- Old.\n"
    )
    assert extract_notes(changelog, "0.2.0") == "- New feature.\n\n### Details\n\nMore.\n"


def test_missing_version_fails_without_matching_a_prefix():
    with pytest.raises(ValueError, match="No CHANGELOG section"):
        extract_notes("## 0.1.0rc1\n\n- Prerelease.\n", "0.1.0")


def test_prerelease_section():
    assert extract_notes("## 0.1.0rc1 (unreleased)\n\n- Preview.\n", "0.1.0rc1") == "- Preview.\n"


@pytest.mark.parametrize("version,success", [("0.1.0", True), ("999.0.0", False)])
def test_script_exit_status(version, success):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/release_notes.py"), version],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is success
    if success:
        assert "Python 3.11" in result.stdout
        assert "Apache-2.0" in result.stdout
        assert result.stderr == ""
    else:
        assert "No CHANGELOG section for version 999.0.0" in result.stderr
        assert result.stdout == ""
