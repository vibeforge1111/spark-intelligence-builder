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
        )
        if not normalized.owner:
            raise ValueError("Gateway route owner must not be empty.")

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
