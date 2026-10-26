"""Authenticated, login-only HTTP adapter. Report submission is not enabled."""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Callable, Mapping
from threading import Lock
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, make_server

from .api106 import APIError, Municipality

StartResponse = Callable[..., Any]


class Service:
    """One operator-triggered login test per process; never return the token."""

    def __init__(
        self, env: Mapping[str, str], factory: Callable[..., Any] = Municipality
    ) -> None:
        self.env = dict(env)
        self.secret = self.env.get("PARKING_HTTP_TOKEN", "")
        if len(self.secret) < 32:
            raise ValueError(
                "A private HTTP token of at least 32 characters is required"
            )
        self.factory = factory
        self.lock = Lock()
        self.consumed = False

    def __call__(
        self, environ: dict[str, Any], start_response: StartResponse
    ) -> list[bytes]:
        method, path = environ.get("REQUEST_METHOD"), environ.get("PATH_INFO")

        def reply(code: str, result: dict[str, Any]) -> list[bytes]:
            body = json.dumps(result, separators=(",", ":")).encode()
            start_response(
                code,
                [
                    ("Content-Type", "application/json"),
                    ("Cache-Control", "no-store"),
                    ("Content-Length", str(len(body))),
                ],
            )
            return [body]

        if method == "GET" and path == "/healthz":
            return reply("200 OK", {"status": "healthy", "submission_enabled": False})
        provided = environ.get("HTTP_AUTHORIZATION", "")
        if not isinstance(provided, str) or not hmac.compare_digest(
            provided, "Bearer " + self.secret
        ):
            return reply("401 Unauthorized", {"status": "unauthorized"})
        if method != "POST":
            return reply("405 Method Not Allowed", {"status": "method_not_allowed"})
        if path == "/v1/reports":
            # This is a code gate, not an env switch. A separately reviewed change is required.
            return reply("409 Conflict", {"status": "submission_disabled"})
        if path != "/v1/login-check":
            return reply("404 Not Found", {"status": "not_found"})
        if environ.get("QUERY_STRING") or environ.get("HTTP_TRANSFER_ENCODING"):
            return reply("400 Bad Request", {"status": "invalid_request"})
        if environ.get("CONTENT_LENGTH", "") not in ("", "0"):
            return reply("400 Bad Request", {"status": "body_not_allowed"})
        credentials = {
            "subscription_key": self.env.get("PARKING_106_SUBSCRIPTION_KEY", ""),
            "login": {
                "UserName": self.env.get("PARKING_106_USERNAME", ""),
                "Password": self.env.get("PARKING_106_PASSWORD", ""),
            },
        }
        if not credentials["subscription_key"] or not all(
            credentials["login"].values()
        ):
            return reply("503 Service Unavailable", {"status": "configuration_missing"})
        with self.lock:
            if self.consumed:
                return reply("409 Conflict", {"status": "login_attempt_consumed"})
            self.consumed = (
                True  # Consume before network, including failed/uncertain attempts.
            )
            try:
                token = self.factory(credentials).login()
                if not token:
                    raise APIError("No token")
            except APIError as error:
                # Do not forward arbitrary server text or secrets. Only known failure classes.
                diagnostic = "login_failed"
                if str(error) == "Login: HTTP 403.":
                    diagnostic = "http_403"
                elif str(error) == "Login: HTTP 401.":
                    diagnostic = "http_401"
                return reply(
                    "502 Bad Gateway",
                    {"status": "login_failed", "diagnostic": diagnostic},
                )
            except Exception:  # noqa: BLE001 - fail-closed boundary; no exception text exposed
                return reply(
                    "502 Bad Gateway",
                    {"status": "login_failed", "diagnostic": "unknown"},
                )
            finally:
                # Token stays inside this call and is never serialized, persisted or logged.
                credentials.clear()
        return reply("200 OK", {"status": "login_ok", "submission_enabled": False})


class QuietHandler(WSGIRequestHandler):
    """Never log request headers, paths, queries or client-supplied input."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def handle(self) -> None:
        self.connection.settimeout(10)
        super().handle()


def main() -> None:
    try:
        app = Service(os.environ)
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError("port")
    except Exception:  # noqa: BLE001 - fail-closed boundary; no exception text exposed
        raise SystemExit(
            "HTTP service configuration is invalid; check private environment"
        ) from None
    with make_server("0.0.0.0", port, app, handler_class=QuietHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
