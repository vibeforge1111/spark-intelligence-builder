from __future__ import annotations

from spark_intelligence.gateway.routes import GatewayRouteRegistration, GatewayRouteRegistry

from tests.test_support import SparkTestCase


class GatewayRouteRegistryTests(SparkTestCase):
    def test_registers_normalized_route(self) -> None:
        registry = GatewayRouteRegistry()

        route = registry.register(
            GatewayRouteRegistration(
                path="/oauth/callback/",
                methods=("get",),
                auth_mode="oauth_callback",
                owner="auth-service",
            )
        )

        self.assertEqual(route.path, "/oauth/callback")
        self.assertEqual(route.methods, ("GET",))
        self.assertEqual(route.owner, "auth-service")
        self.assertEqual(len(registry.list_routes()), 1)

    def test_rejects_conflicting_route_without_replace(self) -> None:
        registry = GatewayRouteRegistry()
        registry.register(
            GatewayRouteRegistration(
                path="/oauth/callback",
                methods=("GET",),
                auth_mode="oauth_callback",
                owner="auth-service",
            )
        )

        with self.assertRaisesRegex(ValueError, "route conflict"):
            registry.register(
                GatewayRouteRegistration(
                    path="/oauth/callback",
                    methods=("GET",),
                    auth_mode="oauth_callback",
                    owner="telegram-adapter",
                )
            )

    def test_allows_owner_replacement_when_requested(self) -> None:
        registry = GatewayRouteRegistry()
        registry.register(
            GatewayRouteRegistration(
                path="/oauth/callback",
                methods=("GET",),
                auth_mode="oauth_callback",
                owner="auth-service",
            )
        )

        route = registry.register(
            GatewayRouteRegistration(
                path="/oauth/callback",
                methods=("GET", "POST"),
                auth_mode="oauth_callback",
                owner="auth-service",
            ),
            replace_existing=True,
        )

        self.assertEqual(route.methods, ("GET", "POST"))
        self.assertEqual(len(registry.list_routes()), 1)

    def test_denies_cross_owner_replacement(self) -> None:
        registry = GatewayRouteRegistry()
        registry.register(
            GatewayRouteRegistration(
                path="/webhooks/telegram",
                methods=("POST",),
                auth_mode="adapter_webhook",
                owner="telegram-adapter",
            )
        )

        with self.assertRaisesRegex(ValueError, "replacement denied"):
            registry.register(
                GatewayRouteRegistration(
                    path="/webhooks/telegram",
                    methods=("POST",),
                    auth_mode="adapter_webhook",
                    owner="gateway-core",
                ),
                replace_existing=True,
            )

    def test_resolves_exact_route_by_path_and_method(self) -> None:
        registry = GatewayRouteRegistry()
        registry.register(
            GatewayRouteRegistration(
                path="/oauth/callback",
                methods=("GET",),
                auth_mode="oauth_callback",
                owner="gateway-core.oauth",
            )
        )

        route = registry.resolve(path="/oauth/callback", method="get")

        self.assertIsNotNone(route)
        self.assertEqual(route.owner, "gateway-core.oauth")

    def test_resolves_longest_prefix_route(self) -> None:
        registry = GatewayRouteRegistry()
        registry.register(
            GatewayRouteRegistration(
                path="/api",
                methods=("POST",),
                auth_mode="provider_internal",
                owner="gateway-core",
                match_mode="prefix",
            )
        )
        registry.register(
            GatewayRouteRegistration(
                path="/api/channels",
                methods=("POST",),
                auth_mode="adapter_webhook",
                owner="channel-adapter",
                match_mode="prefix",
            )
        )

        route = registry.resolve(path="/api/channels/telegram", method="POST")

        self.assertIsNotNone(route)
        self.assertEqual(route.owner, "channel-adapter")
