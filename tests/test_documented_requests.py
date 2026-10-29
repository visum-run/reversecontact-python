"""Audit request literals in shipped examples against the committed API snapshot."""

import ast
import json
import re
from pathlib import Path

import pytest

from reversecontact._http import normalize
from reversecontact._operations import ALIASES

ROOT = Path(__file__).resolve().parents[1]
if (
    not (ROOT.parent / "openapi/public-v2.json").is_file()
    or not (ROOT.parent / "operations.json").is_file()
):
    pytest.skip(
        "Documented request validation requires the monorepo API snapshot and manifest",
        allow_module_level=True,
    )
SPEC = json.loads((ROOT.parent / "openapi/public-v2.json").read_text())
MAPPING = json.loads((ROOT.parent / "operations.json").read_text())["operations"]
OPERATIONS = {
    operation["operationId"]: operation
    for methods in SPEC["paths"].values()
    for operation in methods.values()
    if isinstance(operation, dict) and "operationId" in operation
}


def validate(value, schema):
    if "$ref" in schema:
        schema = SPEC["components"]["schemas"][schema["$ref"].split("/")[-1]]
    for part in schema.get("allOf", []):
        validate(value, part)
    for union in ("anyOf", "oneOf"):
        if union in schema:
            for option in schema[union]:
                try:
                    validate(value, option)
                    break
                except AssertionError:
                    pass
            else:
                raise AssertionError(f"{value!r} does not match {union}")
    if "enum" in schema:
        assert value in schema["enum"]
    kind = schema.get("type")
    if kind == "object":
        assert isinstance(value, dict)
        assert set(schema.get("required", [])).issubset(value)
        for key, item in value.items():
            assert key in schema["properties"], f"Unknown example parameter: {key}"
            validate(item, schema["properties"][key])
    elif kind == "array":
        assert isinstance(value, list)
        for item in value:
            validate(item, schema.get("items", {}))
    elif kind == "string":
        assert isinstance(value, str)
    elif kind in {"integer", "number"}:
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        assert value >= schema.get("minimum", float("-inf"))
        assert value <= schema.get("maximum", float("inf"))
    elif kind == "boolean":
        assert isinstance(value, bool)
    elif kind == "null":
        assert value is None


def test_all_documented_request_literals_match_snapshot():
    readme = (ROOT / "README.md").read_text()
    snippets = re.findall(r"```python\n(.*?)```", readme, re.S)
    snippets += [code for code in re.findall(r"`([^`\n]+)`", readme) if code.startswith("client.")]
    snippets += [path.read_text() for path in sorted((ROOT / "examples").glob("*.py"))]
    snippets += [(ROOT / "tests/test_live.py").read_text()]
    by_method = {entry["py"]: OPERATIONS[key] for key, entry in MAPPING.items()}
    checked = 0
    for snippet in snippets:
        for call in ast.walk(ast.parse(snippet)):
            if not isinstance(call, ast.Call):
                continue
            names = []
            function = call.func
            while isinstance(function, ast.Attribute):
                names.append(function.attr)
                function = function.value
            if not isinstance(function, ast.Name) or function.id != "client":
                continue
            name = ".".join(reversed(names)).replace("iter_", "")
            name = name.replace(".wait", ".retrieve")
            if name not in by_method:
                continue
            operation = by_method[name]
            body = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            values = {}
            for keyword in call.keywords:
                if keyword.arg in {
                    "timeout",
                    "max_retries",
                    "headers",
                    "max_results",
                    "poll_wait",
                    "on_poll",
                    "idempotency_key",
                }:
                    continue
                values[keyword.arg] = ast.literal_eval(keyword.value)
            values = normalize(values, ALIASES)
            if body is not None:
                validate(values, body)
            else:
                query = {
                    param["name"]: param["schema"]
                    for param in operation.get("parameters", [])
                    if param["in"] == "query"
                }
                for key, value in values.items():
                    assert key in query
                    validate(value, query[key])
            if name.startswith("search.") and not name.endswith("_fields"):
                assert set(values) - {"cursor", "perPage"}, f"No filter provided: {name}"
            checked += 1
    assert checked >= 30
