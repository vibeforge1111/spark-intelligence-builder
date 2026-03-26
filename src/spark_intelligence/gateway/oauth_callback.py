from __future__ import annotations

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import monotonic
from urllib.parse import SplitResult, parse_qs, urlsplit, urlunsplit

from spark_intelligence.auth.service import complete_oauth_login_from_callback_url
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway.routes import GatewayRouteRegistration, GatewayRouteRegistry
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class OAuthCallbackCapture:
    callback_url: str
    path: str
    query: str


@dataclass(frozen=True)
class GatewayOAuthCallbackResult:
    callback_url: str
    path: str
    query: str
    provider_id: str
    auth_profile_id: str
    status: str
    default_model: str | None
    base_url: str | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "callback_url": self.callback_url,
                "path": self.path,
                "query": self.query,
                "provider_id": self.provider_id,
                "auth_profile_id": self.auth_profile_id,
                "status": self.status,
                "default_model": self.default_model,
                "base_url": self.base_url,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [
            f"Gateway OAuth callback completed for {self.provider_id}",
            f"- auth_profile: {self.auth_profile_id}",
            f"- status: {self.status}",
            f"- callback_path: {self.path}",
        ]
        if self.default_model:
            lines.append(f"- default_model: {self.default_model}")
        if self.base_url:
            lines.append(f"- base_url: {self.base_url}")
        return "\n".join(lines)


def listen_for_oauth_callback(
    *,
    redirect_uri: str,
    owner: str,
    timeout_seconds: int = 120,
    registry: GatewayRouteRegistry | None = None,
) -> OAuthCallbackCapture:
    return _capture_oauth_callback(
        redirect_uri=redirect_uri,
        owner=owner,
        timeout_seconds=timeout_seconds,
        registry=registry,
    )


def serve_gateway_oauth_callback(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    redirect_uri: str,
    timeout_seconds: int = 120,
    registry: GatewayRouteRegistry | None = None,
    expected_provider: str | None = None,
) -> GatewayOAuthCallbackResult:
    capture = _capture_oauth_callback(
        redirect_uri=redirect_uri,
        owner="gateway-core.oauth",
        timeout_seconds=timeout_seconds,
        registry=registry,
    )
    result = complete_oauth_login_from_callback_url(
        config_manager=config_manager,
        state_db=state_db,
        callback_url=capture.callback_url,
        expected_provider=expected_provider,
    )
    return GatewayOAuthCallbackResult(
        callback_url=capture.callback_url,
        path=capture.path,
        query=capture.query,
        provider_id=result.provider_id,
        auth_profile_id=result.auth_profile_id,
        status=result.status,
        default_model=result.default_model,
        base_url=result.base_url,
    )


def pending_oauth_redirect_uri(
    *,
    state_db: StateDB,
    provider_id: str | None = None,
) -> str | None:
    query = """
        SELECT redirect_uri
        FROM oauth_callback_states
        WHERE status = 'pending'
    """
    params: list[str] = []
    if provider_id:
        query += " AND provider_id = ?"
        params.append(provider_id)
    query += " ORDER BY created_at DESC LIMIT 1"
    with state_db.connect() as conn:
        row = conn.execute(query, tuple(params)).fetchone()
    if not row or not row["redirect_uri"]:
        return None
    return str(row["redirect_uri"])


def callback_state_from_capture(capture: OAuthCallbackCapture) -> str:
    query = parse_qs(capture.query)
    states = query.get("state") or []
    if not states or not states[0]:
        raise ValueError("OAuth callback URL is missing 'state'.")
    return states[0]


def _capture_oauth_callback(
    *,
    redirect_uri: str,
    owner: str,
    timeout_seconds: int,
    registry: GatewayRouteRegistry | None,
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
    server = HTTPServer(
        (parsed.hostname or "127.0.0.1", parsed.port or 80),
        _build_handler(parsed=parsed, capture=capture, registry=route_registry),
    )
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


def _build_handler(*, parsed: SplitResult, capture: dict[str, str], registry: GatewayRouteRegistry):
    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request_target = urlsplit(self.path)
            route = registry.resolve(path=request_target.path, method="GET")
            if not route or route.path != parsed.path:
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
