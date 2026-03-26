from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


GatewayRouteAuthMode = Literal["operator", "oauth_callback", "provider_internal", "adapter_webhook"]
GatewayRouteMatchMode = Literal["exact", "prefix"]


@dataclass(frozen=True)
class GatewayRouteRegistration:
    path: str
    methods: tuple[str, ...]
    auth_mode: GatewayRouteAuthMode
    owner: str
    match_mode: GatewayRouteMatchMode = "exact"
    content_types: tuple[str, ...] = ()


class GatewayRouteRegistry:
    def __init__(self) -> None:
        self._routes: list[GatewayRouteRegistration] = []

    def register(self, route: GatewayRouteRegistration, *, replace_existing: bool = False) -> GatewayRouteRegistration:
        normalized = GatewayRouteRegistration(
            path=_normalize_path(route.path),
            methods=_normalize_methods(route.methods),
            auth_mode=route.auth_mode,
            owner=route.owner.strip(),
            match_mode=route.match_mode,
            content_types=_normalize_content_types(route.content_types),
        )
        if not normalized.owner:
            raise ValueError("Gateway route owner must not be empty.")
        _validate_registration(normalized)

        existing_index = self._find_conflict_index(normalized)
        if existing_index is None:
            self._routes.append(normalized)
            return normalized

        existing = self._routes[existing_index]
        if not replace_existing:
            raise ValueError(
                f"Gateway route conflict at {normalized.path} owned by {existing.owner}."
            )
        if existing.owner != normalized.owner:
            raise ValueError(
                f"Gateway route replacement denied for {normalized.path}; owned by {existing.owner}."
            )
        self._routes[existing_index] = normalized
        return normalized

    def list_routes(self) -> list[GatewayRouteRegistration]:
        return list(self._routes)

    def resolve(self, *, path: str, method: str) -> GatewayRouteRegistration | None:
        normalized_path = _normalize_path(path)
        normalized_method = _normalize_methods((method,))[0]

        for route in self._routes:
            if route.match_mode == "exact" and route.path == normalized_path and normalized_method in route.methods:
                return route

        prefix_matches = [
            route
            for route in self._routes
            if route.match_mode == "prefix"
            and normalized_path.startswith(route.path)
            and normalized_method in route.methods
        ]
        if not prefix_matches:
            return None
        prefix_matches.sort(key=lambda route: len(route.path), reverse=True)
        return prefix_matches[0]

    def validate_request(
        self,
        *,
        path: str,
        method: str,
        content_type: str | None = None,
    ) -> GatewayRouteRegistration:
        normalized_path = _normalize_path(path)
        normalized_method = _normalize_methods((method,))[0]
        route = self.resolve(path=normalized_path, method=normalized_method)
        if route is None:
            allowed_methods = self.allowed_methods_for_path(path=normalized_path)
            if allowed_methods:
                raise ValueError(
                    f"Gateway route {normalized_path} rejects method '{normalized_method}'."
                )
            raise ValueError("Gateway route not found.")
        if route.content_types:
            normalized_content_type = _normalize_request_content_type(content_type)
            if not normalized_content_type:
                raise ValueError(
                    f"Gateway route {route.path} requires Content-Type {', '.join(route.content_types)}."
                )
            if normalized_content_type not in route.content_types:
                raise ValueError(
                    f"Gateway route {route.path} rejects Content-Type '{normalized_content_type}'."
                )
        return route

    def allowed_methods_for_path(self, *, path: str) -> tuple[str, ...]:
        normalized_path = _normalize_path(path)
        methods = {
            method
            for route in self._routes
            if _path_matches(route=route, path=normalized_path)
            for method in route.methods
        }
        return tuple(sorted(methods))

    def _find_conflict_index(self, candidate: GatewayRouteRegistration) -> int | None:
        for index, existing in enumerate(self._routes):
            if existing.path != candidate.path or existing.match_mode != candidate.match_mode:
                continue
            if set(existing.methods) & set(candidate.methods):
                return index
        return None


def _normalize_path(path: str) -> str:
    value = path.strip()
    if not value:
        raise ValueError("Gateway route path must not be empty.")
    if not value.startswith("/"):
        raise ValueError("Gateway route path must start with '/'.")
    if len(value) > 1 and value.endswith("/"):
        value = value.rstrip("/")
    return value


def _normalize_methods(methods: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({method.strip().upper() for method in methods if method.strip()}))
    if not normalized:
        raise ValueError("Gateway route must declare at least one HTTP method.")
    return normalized


def _normalize_content_types(content_types: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_request_content_type(content_type) for content_type in content_types if content_type.strip()}))


def _normalize_request_content_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _validate_registration(route: GatewayRouteRegistration) -> None:
    if route.auth_mode == "oauth_callback":
        if route.methods != ("GET",):
            raise ValueError("OAuth callback routes must use GET only.")
        if route.content_types:
            raise ValueError("OAuth callback routes must not declare request body content types.")
        return
    if route.auth_mode == "adapter_webhook":
        if route.methods != ("POST",):
            raise ValueError("Adapter webhook routes must use POST only.")
        if not route.content_types:
            raise ValueError("Adapter webhook routes must declare at least one request content type.")


def _path_matches(*, route: GatewayRouteRegistration, path: str) -> bool:
    if route.match_mode == "exact":
        return route.path == path
    return path.startswith(route.path)
