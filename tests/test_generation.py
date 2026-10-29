import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("present", [None, "openapi/public-v2.json", "operations.json"])
def test_generation_skips_without_monorepo_inputs(tmp_path, present):
    sdk = tmp_path / "python"
    scripts = sdk / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/generate.py", scripts / "generate.py")
    if present is not None:
        path = tmp_path / present
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    result = subprocess.run(
        [sys.executable, "scripts/generate.py", "--check"],
        cwd=sdk,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "SKIP generation" in result.stdout
    assert "only in the monorepo" in result.stdout
    assert not (sdk / "src").exists()


def test_generated_files_are_current():
    if (
        not (ROOT.parent / "openapi/public-v2.json").is_file()
        or not (ROOT.parent / "operations.json").is_file()
    ):
        pytest.skip("Generated-file validation requires the monorepo API snapshot and manifest")
    result = subprocess.run(
        [sys.executable, "scripts/generate.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_python311_syntax():
    for directory in ("src", "tests", "scripts", "examples"):
        for path in (ROOT / directory).rglob("*.py"):
            ast.parse(path.read_text(), filename=str(path), feature_version=(3, 11))
