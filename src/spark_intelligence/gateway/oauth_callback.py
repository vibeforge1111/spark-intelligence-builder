from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import monotonic
from urllib.parse import SplitResult, urlsplit, urlunsplit

from spark_intelligence.gateway.routes import GatewayRouteRegistration, GatewayRouteRegistry


@dataclass(frozen=True)
class OAuthCallbackCapture:
    callback_url: str
    path: str
    query: str


def listen_for_oauth_callback(
    *,
    redirect_uri: str,
    owner: str,
    timeout_seconds: int = 120,
    registry: GatewayRouteRegistry | None = None,
) -> OAuthCallbackCapture:
    parsed = _validate_redirect_uri(redirect_uri)
    route_registry = registry or GatewayRouteRegistry()
    route_registry.register(
        GatewayRouteRegistration(
            path=parsed.path,
            methods=("GET",),
            auth_mode="oauth_callback",
            owner=owner,
        )
    )

    capture: dict[str, str] = {}
    server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or 80), _build_handler(parsed=parsed, capture=capture))
    deadline = monotonic() + max(timeout_seconds, 1)
    try:
        while "callback_url" not in capture and monotonic() < deadline:
            remaining = deadline - monotonic()
            server.timeout = min(max(remaining, 0.1), 1.0)
            server.handle_request()
    finally:
        server.server_close()

    if "callback_url" not in capture:
        raise TimeoutError(f"Timed out waiting for OAuth callback on {redirect_uri}.")
    return OAuthCallbackCapture(
        callback_url=capture["callback_url"],
        path=capture["path"],
        query=capture["query"],
    )


def _build_handler(*, parsed: SplitResult, capture: dict[str, str]):
    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request_target = urlsplit(self.path)
            if request_target.path != parsed.path:
                self.send_error(404, "Route not found.")
                return

            callback_url = urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    request_target.path,
                    request_target.query,
                    "",
                )
            )
            capture["callback_url"] = callback_url
            capture["path"] = request_target.path
            capture["query"] = request_target.query

            body = (
                "<html><body><h1>OAuth callback captured</h1>"
                "<p>You can return to the Spark Intelligence terminal.</p></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            self.send_error(405, "Method not allowed.")

        def log_message(self, format: str, *args: object) -> None:
            return

    return OAuthCallbackHandler


def _validate_redirect_uri(redirect_uri: str) -> SplitResult:
    parsed = urlsplit(redirect_uri)
    if parsed.scheme != "http":
        raise ValueError("OAuth callback listener requires an http:// redirect URI.")
    if not parsed.hostname or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("OAuth callback listener only supports loopback redirect URIs.")
    if not parsed.path.startswith("/"):
        raise ValueError("OAuth callback listener redirect URI must include an absolute path.")
    return parsed
