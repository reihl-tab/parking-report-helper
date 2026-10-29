"""docs/openapi.json must match http_service.Service exactly (#72).

Offline only: the municipal client is a fake, and nothing leaves the process.
The schema check is a small subset validator (type, const, enum, required,
properties, additionalProperties, $ref), so no new test dependency is needed.
"""

import ast
import io
import json
import unittest
from pathlib import Path

from parking_report import http_service
from parking_report.api106 import APIError
from parking_report.http_service import Service

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "docs" / "openapi.json"
# The installed module, not src/: the Docker test stage has no src tree.
SOURCE_PATH = Path(http_service.__file__)

TOKEN = "synthetic-only-token-0000000000000000"
ENV = {
    "PARKING_HTTP_TOKEN": TOKEN,
    "PARKING_106_SUBSCRIPTION_KEY": "fake-key",
    "PARKING_106_USERNAME": "fake-user",
    "PARKING_106_PASSWORD": "fake-password",
}

# Responses for requests outside the documented operations (info.description).
FALLBACK_SCHEMAS = {
    401: "Unauthorized",
    404: "NotFound",
    405: "MethodNotAllowed",
}


def fake_factory(outcome):
    class Fake:
        def __init__(self, credentials):
            pass

        def login(self):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    return Fake


# (path, method, auth, extra environ, login outcome, env, calls before, expected code)
SCENARIOS = [
    ("/healthz", "GET", False, {}, "tok", ENV, 0, 200),
    ("/healthz", "GET", True, {}, "tok", ENV, 0, 200),
    ("/v1/login-check", "POST", True, {}, "tok", ENV, 0, 200),
    ("/v1/login-check", "POST", True, {"QUERY_STRING": "a=b"}, "tok", ENV, 0, 400),
    ("/v1/login-check", "POST", True, {"CONTENT_LENGTH": "5"}, "tok", ENV, 0, 400),
    (
        "/v1/login-check",
        "POST",
        True,
        {"HTTP_TRANSFER_ENCODING": "chunked"},
        "tok",
        ENV,
        0,
        400,
    ),
    ("/v1/login-check", "POST", False, {}, "tok", ENV, 0, 401),
    ("/v1/login-check", "POST", True, {}, "tok", ENV, 1, 409),
    ("/v1/login-check", "POST", True, {}, APIError("Login: HTTP 403."), ENV, 0, 502),
    ("/v1/login-check", "POST", True, {}, APIError("Login: HTTP 401."), ENV, 0, 502),
    ("/v1/login-check", "POST", True, {}, APIError("other"), ENV, 0, 502),
    ("/v1/login-check", "POST", True, {}, ValueError("x"), ENV, 0, 502),
    ("/v1/login-check", "POST", True, {}, "", ENV, 0, 502),
    (
        "/v1/login-check",
        "POST",
        True,
        {},
        "tok",
        {"PARKING_HTTP_TOKEN": TOKEN},
        0,
        503,
    ),
    ("/v1/reports", "POST", True, {}, "tok", ENV, 0, 409),
    ("/v1/reports", "POST", True, {"CONTENT_LENGTH": "5"}, "tok", ENV, 0, 409),
    ("/v1/reports", "POST", False, {}, "tok", ENV, 0, 401),
    # Routing fallbacks.
    ("/healthz", "POST", False, {}, "tok", ENV, 0, 401),
    ("/healthz", "HEAD", True, {}, "tok", ENV, 0, 405),
    ("/v1/login-check", "GET", True, {}, "tok", ENV, 0, 405),
    ("/v1/reports", "DELETE", True, {}, "tok", ENV, 0, 405),
    ("/unknown", "GET", False, {}, "tok", ENV, 0, 401),
    ("/unknown", "POST", True, {}, "tok", ENV, 0, 404),
]


def load_spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def resolve(spec, node):
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise AssertionError("external $ref " + ref)
        node = spec
        for part in ref[2:].split("/"):
            node = node[part]
    return node


def validate(spec, schema, value, where="$"):
    """Raise AssertionError if value does not match the schema subset."""
    schema = resolve(spec, schema)
    kind = schema.get("type")
    checks = {
        "object": lambda v: isinstance(v, dict),
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
    }
    if kind is not None and not checks[kind](value):
        raise AssertionError(f"{where}: expected {kind}, got {value!r}")
    if "const" in schema and value != schema["const"]:
        raise AssertionError(f"{where}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise AssertionError(f"{where}: {value!r} not in {schema['enum']!r}")
    if kind == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise AssertionError(f"{where}: missing {key!r}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                raise AssertionError(f"{where}: undocumented keys {sorted(extra)}")
        for key, sub in props.items():
            if key in value:
                validate(spec, sub, value[key], f"{where}.{key}")


def call(scenario):
    path, method, auth, extra, outcome, env, before, _ = scenario
    app = Service(env, fake_factory(outcome))
    app.consumed = bool(before)
    environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "wsgi.input": io.BytesIO()}
    if auth:
        environ["HTTP_AUTHORIZATION"] = "Bearer " + TOKEN
    environ.update(extra)
    seen = []
    body = b"".join(
        app(environ, lambda status, headers: seen.append((status, headers)))
    )
    status, headers = seen[0]
    return int(status.split()[0]), dict(headers), json.loads(body)


def source_literals():
    """Paths compared against PATH_INFO and status/diagnostic values in the source."""
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    paths, values = set(), {"status": set(), "diagnostic": set()}
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and getattr(node.left, "id", None) == "path":
            for comp in node.comparators:
                if isinstance(comp, ast.Constant):
                    paths.add(comp.value)
        if isinstance(node, ast.Dict):
            for key, val in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value in values
                    and isinstance(val, ast.Constant)
                ):
                    values[key.value].add(val.value)
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "diagnostic" for t in node.targets
        ):
            if isinstance(node.value, ast.Constant):
                values["diagnostic"].add(node.value.value)
    return paths, values


def schema_values(spec, field):
    found = set()
    for schema in spec["components"]["schemas"].values():
        prop = schema.get("properties", {}).get(field)
        if prop:
            found.update(prop.get("enum", []))
            if "const" in prop:
                found.add(prop["const"])
    return found


class OpenAPIContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec()

    def operation(self, path, method):
        item = self.spec["paths"].get(path, {})
        return item.get(method.lower())

    def test_spec_header_and_refs(self):
        self.assertTrue(self.spec["openapi"].startswith("3."))
        text = SPEC_PATH.read_text(encoding="utf-8")

        def walk(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    resolve(self.spec, node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.spec)
        self.assertNotIn(TOKEN, text)

    def test_every_scenario_matches_spec(self):
        for scenario in SCENARIOS:
            path, method, expected = scenario[0], scenario[1], scenario[-1]
            with self.subTest(path=path, method=method, code=expected):
                code, headers, body = call(scenario)
                self.assertEqual(code, expected)
                self.assertEqual(headers["Content-Type"], "application/json")
                self.assertEqual(headers["Cache-Control"], "no-store")
                op = self.operation(path, method)
                if op is not None and str(code) in op["responses"]:
                    response = resolve(self.spec, op["responses"][str(code)])
                    schema = response["content"]["application/json"]["schema"]
                else:
                    self.assertIn(code, FALLBACK_SCHEMAS, "undocumented response")
                    schema = {"$ref": "#/components/schemas/" + FALLBACK_SCHEMAS[code]}
                validate(self.spec, schema, body)

    def test_every_documented_response_is_exercised(self):
        exercised = {(s[0], s[1].lower(), str(s[-1])) for s in SCENARIOS}
        for path, item in self.spec["paths"].items():
            for method, op in item.items():
                for code in op["responses"]:
                    with self.subTest(path=path, method=method, code=code):
                        self.assertIn((path, method, code), exercised)

    def test_spec_paths_equal_routed_paths(self):
        paths, _ = source_literals()
        self.assertEqual(set(self.spec["paths"]), paths)

    def test_every_emitted_value_is_documented(self):
        _, values = source_literals()
        for field, emitted in values.items():
            with self.subTest(field=field):
                self.assertTrue(emitted)
                self.assertEqual(schema_values(self.spec, field), emitted)

    def test_validator_rejects_drift(self):
        # Guard the guard: the subset validator must catch the drifts it exists for.
        bad = [
            ("Health", {"status": "healthy", "submission_enabled": True}),
            ("Health", {"status": "healthy"}),
            (
                "LoginOk",
                {"status": "login_ok", "submission_enabled": False, "token": "x"},
            ),
            ("LoginFailed", {"status": "login_failed", "diagnostic": "server text"}),
        ]
        for name, body in bad:
            with self.subTest(name=name, body=body):
                with self.assertRaises(AssertionError):
                    validate(self.spec, {"$ref": "#/components/schemas/" + name}, body)


if __name__ == "__main__":
    unittest.main()
