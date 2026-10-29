"""Generate models and typed resource facades from the pinned OpenAPI snapshot."""

import json
import keyword
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT.parent / "openapi/public-v2.json"
MAP_PATH = ROOT.parent / "operations.json"
if not SPEC_PATH.is_file() or not MAP_PATH.is_file():
    print("SKIP generation: API snapshot and operation manifest are available only in the monorepo")
    raise SystemExit(0)
SPEC = json.loads(SPEC_PATH.read_text())
SCHEMAS = SPEC["components"]["schemas"]
MAP = json.loads(MAP_PATH.read_text())["operations"]
DESTINATION = ROOT / "src/reversecontact"
CHECK = "--check" in sys.argv
temporary = tempfile.TemporaryDirectory(prefix=".generated-", dir=ROOT) if CHECK else None
OUT = Path(temporary.name) if temporary is not None else DESTINATION
(OUT / "types").mkdir(parents=True, exist_ok=True)
(OUT / "resources").mkdir(parents=True, exist_ok=True)
models = {}
aliases = {}


def snake(name):
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower().replace("-", "_")
    return (
        value + "_"
        if keyword.iskeyword(value) or value in {"model_config", "model_fields"}
        else value
    )


def pascal(name):
    return name[0].upper() + name[1:]


def resolve(schema):
    if "$ref" in schema:
        return resolve(SCHEMAS[schema["$ref"].split("/")[-1]])
    if "allOf" in schema:
        result = {"type": "object", "properties": {}}
        for part in schema["allOf"]:
            result["properties"].update(resolve(part).get("properties", {}))
        return result
    return schema


def annotation(schema, name, *, request=False):
    if "$ref" in schema:
        ref = schema["$ref"].split("/")[-1]
        register(ref, SCHEMAS[ref])
        return ref
    if "allOf" in schema:
        schema = resolve(schema)
    variants = schema.get("anyOf", schema.get("oneOf"))
    if variants:
        values = list(
            dict.fromkeys(
                annotation(v, name, request=request) for v in variants if v.get("type") != "null"
            )
        )
        return values[0] if len(values) == 1 else " | ".join(values) if values else "object"
    kind = schema.get("type")
    if kind == "array":
        item = annotation(schema.get("items", {}), name + "Item", request=request)
        return f"list[{item}]" if request else f"list[{item} | None]"
    if kind == "object" or "properties" in schema:
        if schema.get("properties"):
            register(name, schema)
            return f"{name} | dict[str, object]" if request else name
        return "dict[str, object]"
    return {
        "string": "str",
        "integer": "int",
        "number": "int | float",
        "boolean": "bool",
        "null": "None",
    }.get(kind, "object")


def register(name, schema):
    if name in models:
        return
    models[name] = []
    schema = resolve(schema)
    for field, child in schema.get("properties", {}).items():
        py = snake(field)
        aliases[py] = field
        hint = annotation(child, name + pascal(field))
        models[name].append(f"    {py}: {hint} | None = Field(None, alias={field!r})")


for name, schema in SCHEMAS.items():
    if name.startswith("V2"):
        continue
    register(name, schema)

ops = {}
for path, methods in SPEC["paths"].items():
    for method, operation in methods.items():
        if "operationId" not in operation:
            continue
        name = operation["operationId"]
        entry = MAP[name]
        body = resolve(
            operation.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        properties = dict(body.get("properties", {}))
        paths = []
        for param in operation.get("parameters", []):
            if param["in"] == "path":
                paths.append(param["name"])
            elif param["in"] == "query":
                properties[param["name"]] = param["schema"]
        success = next(v for k, v in operation["responses"].items() if k.startswith("2"))
        schema = resolve(success.get("content", {}).get("application/json", {}).get("schema", {}))
        data = schema.get("properties", {}).get("data", {})
        result = annotation(data, pascal(name) + "Data")
        if entry["kind"] == "page":
            item_schema = resolve(data)["properties"]["data"]["items"]
            item = annotation(item_schema, pascal(name) + "Item")
        else:
            item = None
        params = []
        for field, child in properties.items():
            aliases[snake(field)] = field
            params.append(
                (field, annotation(child, pascal(name) + "Params" + pascal(field), request=True))
            )
        ops[name] = dict(
            entry,
            path=path,
            method=method.upper(),
            paths=paths,
            params=params,
            result=result,
            item=item,
            body="requestBody" in operation,
        )

header = (
    "# Generated by scripts/generate.py. Do not edit by hand.\nfrom __future__ import annotations\n"
)
model_text = header + "from pydantic import Field\n" + "from .._models import Model\n\n"
for name, fields in models.items():
    model_text += f"class {name}(Model):\n" + ("\n".join(fields) or "    pass") + "\n\n"
model_text += "\n".join(f"{name}.model_rebuild()" for name in models) + "\n"
model_text = model_text.replace("list[", "builtins.list[").replace(
    "from __future__ import annotations", "from __future__ import annotations\nimport builtins"
)
(OUT / "types/_generated.py").write_text(model_text)
(OUT / "types/__init__.py").write_text(
    header.replace("from __future__ import annotations\n", "")
    + "\n".join(f"from ._generated import ({name},)" for name in models)
    + "\nfrom ._generated import PersonFull as Person\n"
    + "from ._generated import CompanyFull as Company\n__all__ = "
    + repr(sorted([*models, "Person", "Company"]))
    + "\n"
)

registry = (
    header.replace("from __future__ import annotations\n", "")
    + "from ._http import Operation\n\nALIASES: dict[str, str] = "
    + repr(aliases)
    + "\n\nOPERATIONS = {\n"
)
for name, op in ops.items():
    flags = {
        "has_body": op["body"],
        "lookup": name in {"enrichPerson", "enrichCompany", "fetchPerson", "fetchCompany"},
        "idempotency": name in {"findContactEmail", "findContactPhone"},
        "poll": name == "pollWebhook",
    }
    arguments = {"method": op["method"], "path": op["path"], **flags}
    registry += (
        f"{name!r}: Operation("
        + ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        + "),\n"
    )
registry += "}\n"
(OUT / "_operations.py").write_text(registry)

resource_names = [
    "Enrich",
    "Fetch",
    "Search",
    "Contact",
    "Jobs",
    "Usage",
]
for asynchronous in (False, True):
    prefix = "Async" if asynchronous else ""
    await_ = "await " if asynchronous else ""
    def_ = "async def" if asynchronous else "def"
    text = (
        header
        + """from collections.abc import Callable, Iterator, AsyncIterator, Mapping
from .. import types as models
from .._models import Result, Model, ContactValue, JobSnapshot
from .._http import NOT_GIVEN, NotGiven, Timeout, RequestOptions, parameters
from .._operations import OPERATIONS
from .._resource import SyncResource, AsyncResource
from .._pagination import Page, AsyncPage
from .._jobs import Job, AsyncJob
"""
    )
    for resource in resource_names:
        base = (
            prefix + resource + "Helpers"
            if resource == "Jobs"
            else prefix + "Resource"
            if asynchronous
            else "SyncResource"
        )
        if resource == "Jobs":
            text += f"from .._jobs import {base}\n"
    for resource in resource_names:
        group = {
            name: op
            for name, op in ops.items()
            if (op["py"].split(".")[-2] if op["py"].count(".") > 1 else op["py"].split(".")[0])
            == resource.lower()
        }
        base = (
            prefix + resource + "Helpers"
            if resource == "Jobs"
            else "AsyncResource"
            if asynchronous
            else "SyncResource"
        )
        text += f"\nclass {prefix}{resource}({base}):\n"
        for name, op in group.items():
            method_name = op["py"].split(".")[-1]
            signatures = [f"{snake(p)}: str" for p in op["paths"]]
            signatures += ["*"]
            for field, hint in op["params"]:
                # Qualify generated model types, leaving standard types alone.
                for model in sorted(models, key=len, reverse=True):
                    hint = re.sub(r"\b" + model + r"\b", "models." + model, hint)
                signatures.append(f"{snake(field)}: ({hint} | NotGiven | None) = NOT_GIVEN")
            signatures += [
                "timeout: Timeout | None = None",
                "max_retries: int | None = None",
                "headers: Mapping[str, str] | None = None",
            ]
            if name in {"findContactEmail", "findContactPhone"}:
                signatures += ["idempotency_key: str | None = None"]
            signatures += ["**extra: object"]
            known = (
                "{" + ", ".join(f"{field!r}: {snake(field)}" for field, hint in op["params"]) + "}"
            )
            path_values = "{" + ", ".join(f"{p!r}: {snake(p)}" for p in op["paths"]) + "}"
            options = (
                "RequestOptions(timeout, max_retries, headers"
                + (", idempotency_key" if name in {"findContactEmail", "findContactPhone"} else "")
                + ")"
            )
            args = f"OPERATIONS[{name!r}], {path_values}, parameters({known}, extra), {options}"
            result = "None" if op["result"] == "None" else "models." + op["result"]
            parser = "(lambda value: None)" if result == "None" else result + ".model_validate"
            if op["kind"] == "page":
                returned = f"{prefix}Page[models.{op['item']}]"
                invocation = f"self._page({args}, models.{op['item']}.model_validate)"
            elif op["kind"] == "job":
                job_type = {
                    "livePerson": "models.PersonFull",
                    "liveCompany": "models.CompanyFull",
                    "findContactEmail": "list[ContactValue | None]",
                    "findContactPhone": "list[ContactValue | None]",
                }[name]
                job_parser = (
                    f"{job_type}.model_validate"
                    if not job_type.startswith("list")
                    else "parse_contacts"
                )
                returned = f"{prefix}Job[{job_type}]"
                invocation = f"self._job({args}, {job_parser})"
            else:
                returned = f"Result[{result}]"
                if name in {"enrichPerson", "enrichCompany", "fetchPerson", "fetchCompany"}:
                    returned = f"{returned} | None"
                    invocation = f"self._lookup({args}, {parser})"
                else:
                    invocation = f"self._result({args}, {parser})"
            text += (
                f"    {def_} {method_name}(self, " + ", ".join(signatures) + f") -> {returned}:\n"
            )
            text += f'        """Call {name}. Extra arguments pass through."""\n'
            text += f"        return {await_}{invocation}\n\n"
            if op["kind"] == "page":
                helper = "iter_" + method_name
                iter_signatures = [s for s in signatures if not s.startswith("**")]
                iter_signatures += [
                    "max_results: int",
                    "**extra: object",
                ]
                iterator = "AsyncIterator" if asynchronous else "Iterator"
                text += (
                    f"    def {helper}(self, "
                    + ", ".join(iter_signatures)
                    + f") -> {iterator}[models.{op['item']} | None]:\n"
                )
                iteration = (
                    f"self._iterate({args}, models.{op['item']}.model_validate, max_results)"
                )
                text += f"        return {iteration}\n\n"
    text = text.replace(
        "from .._models import",
        "from .._jobs import parse_contacts\nfrom .._models import",
    )
    text = text.replace("list[", "builtins.list[").replace(
        "from __future__ import annotations", "from __future__ import annotations\nimport builtins"
    )
    text = text.replace("from __future__ import annotations\n", "")
    (OUT / f"resources/{'async_' if asynchronous else 'sync'}.py").write_text(text)
(OUT / "resources/__init__.py").write_text('"""Typed resource groups for both clients."""\n')
subprocess.run(
    [
        sys.executable,
        "-m",
        "ruff",
        "format",
        str(OUT / "types"),
        str(OUT / "_operations.py"),
        str(OUT / "resources"),
    ],
    check=True,
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--fix",
        "--select",
        "I,F",
        "--output-format",
        "concise",
        str(OUT / "types"),
        str(OUT / "_operations.py"),
        str(OUT / "resources"),
    ],
    check=True,
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "ruff",
        "format",
        str(OUT / "types"),
        str(OUT / "_operations.py"),
        str(OUT / "resources"),
    ],
    check=True,
)

if temporary is not None:
    stale = [
        str(path.relative_to(OUT))
        for path in OUT.rglob("*.py")
        if not (DESTINATION / path.relative_to(OUT)).is_file()
        or path.read_bytes() != (DESTINATION / path.relative_to(OUT)).read_bytes()
    ]
    temporary.cleanup()
    if stale:
        raise SystemExit("Generated files are stale: " + ", ".join(stale))
    print("Generated files match the snapshot and operation manifest")
