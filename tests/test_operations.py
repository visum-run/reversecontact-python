import json
import re
from pathlib import Path
from urllib.parse import quote

import pytest

from conftest import ok

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "openapi/public-v2.json").is_file() or not (ROOT / "operations.json").is_file():
    pytest.skip(
        "Operation coverage requires the monorepo API snapshot and manifest",
        allow_module_level=True,
    )
SPEC = json.loads((ROOT / "openapi/public-v2.json").read_text())
MAPPING = json.loads((ROOT / "operations.json").read_text())["operations"]
OPERATIONS = {
    operation["operationId"]: (method, path, operation)
    for path, methods in SPEC["paths"].items()
    for method, operation in methods.items()
    if "operationId" in operation
}


def resolve(schema):
    if "$ref" in schema:
        return resolve(SPEC["components"]["schemas"][schema["$ref"].split("/")[-1]])
    if "allOf" in schema:
        properties = {}
        for part in schema["allOf"]:
            properties.update(resolve(part).get("properties", {}))
        return {"type": "object", "properties": properties}
    return schema


def sample(schema):
    schema = resolve(schema)
    if "enum" in schema:
        return schema["enum"][0]
    for union in ["anyOf", "oneOf"]:
        if union in schema:
            return sample(schema[union][0])
    kind = schema.get("type")
    if kind == "object":
        return {key: sample(value) for key, value in schema.get("properties", {}).items()}
    if kind == "array":
        return [sample(schema.get("items", {}))]
    return {"string": "test", "number": 1, "integer": 1, "boolean": True, "null": None}.get(kind)


def snake(value):
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()


def snake_keys(value):
    if isinstance(value, dict):
        return {snake(key): snake_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [snake_keys(item) for item in value]
    return value


def get_method(client, path):
    for name in path.split("."):
        client = getattr(client, name)
    return client


def test_manifest_matches_snapshot():
    assert set(MAPPING) == set(OPERATIONS)
    assert len(MAPPING) == 21


@pytest.mark.parametrize("operation_id", MAPPING)
@pytest.mark.parametrize("camel_case", [False, True], ids=["snake", "camel"])
async def test_every_operation_wire_contract(h, operation_id, camel_case):
    method, path, operation = OPERATIONS[operation_id]
    entry = MAPPING[operation_id]
    fn = get_method(h.client, entry["py"])
    assert callable(fn)
    arguments = []
    expected = {}
    body = resolve(
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    expected.update({name: sample(schema) for name, schema in body.get("properties", {}).items()})
    for param in operation.get("parameters", []):
        if param["in"] == "path":
            value = "id/with space?%#"
            arguments.append(value)
            path = path.replace("{" + param["name"] + "}", quote(value, safe=""))
        elif param["in"] == "query":
            expected[param["name"]] = sample(param["schema"])
    expected["future_field"] = "passed-through"
    kwargs = expected if camel_case else snake_keys(expected)
    if entry["kind"] == "job":
        h.responses.append(ok({"webhookId": "job_test", "status": "created"}))
    else:
        h.responses.append(ok(None if operation_id == "getUsage" else {}))
    await h.call(fn, *arguments, **kwargs)
    request = h.requests[-1]
    assert request.method == method.upper()
    assert request.url.raw_path.split(b"?")[0].decode() == path
    if "requestBody" in operation:
        assert json.loads(request.content) == expected
    else:
        assert dict(request.url.params) == {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in expected.items()
            if value is not None
        }
    assert request.headers["authorization"] == "Bearer rc_test"
    assert request.headers["user-agent"].startswith("reversecontact-python/0.1.0")
    assert "x-request-origin" not in request.headers
    assert not re.search(r"\b(make|zapier)\b", request.headers["user-agent"], re.I)


@pytest.mark.parametrize(
    "operation_id", [name for name in MAPPING if MAPPING[name]["kind"] == "sync"]
)
async def test_full_snapshot_response_shapes(h, operation_id):
    _, _, operation = OPERATIONS[operation_id]
    success = next(value for code, value in operation["responses"].items() if code.startswith("2"))
    schema = resolve(success["content"]["application/json"]["schema"])["properties"]["data"]
    payload = sample(schema)
    h.responses.append(ok(payload))
    args = ["test" for param in operation.get("parameters", []) if param["in"] == "path"]
    response = await h.call(get_method(h.client, MAPPING[operation_id]["py"]), *args)
    assert response.data.to_dict() == payload if payload is not None else response.data is None
