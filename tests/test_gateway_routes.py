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
        self.assertEqual(route.content_types, ())
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
                methods=("GET",),
                auth_mode="oauth_callback",
                owner="auth-service",
            ),
            replace_existing=True,
        )

        self.assertEqual(route.methods, ("GET",))
        self.assertEqual(len(registry.list_routes()), 1)

    def test_denies_cross_owner_replacement(self) -> None:
        registry = GatewayRouteRegistry()
        registry.register(
            GatewayRouteRegistration(
                path="/webhooks/telegram",
                methods=("POST",),
                auth_mode="adapter_webhook",
                owner="telegram-adapter",
                content_types=("application/json",),
            )
        )

        with self.assertRaisesRegex(ValueError, "replacement denied"):
            registry.register(
                GatewayRouteRegistration(
                    path="/webhooks/telegram",
                    methods=("POST",),
                    auth_mode="adapter_webhook",
                    owner="gateway-core",
                    content_types=("application/json",),
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
                content_types=("application/json",),
            )
        )

        route = registry.resolve(path="/api/channels/telegram", method="POST")

        self.assertIsNotNone(route)
        self.assertEqual(route.owner, "channel-adapter")

    def test_rejects_adapter_webhook_without_content_type_contract(self) -> None:
        registry = GatewayRouteRegistry()

        with self.assertRaisesRegex(ValueError, "request content type"):
            registry.register(
                GatewayRouteRegistration(
                    path="/webhooks/discord",
                    methods=("POST",),
                    auth_mode="adapter_webhook",
                    owner="discord-adapter",
                )
            )

    def test_rejects_oauth_callback_with_post_method(self) -> None:
        registry = GatewayRouteRegistry()

        with self.assertRaisesRegex(ValueError, "GET only"):
            registry.register(
                GatewayRouteRegistration(
                    path="/auth/callback",
                    methods=("POST",),
                    auth_mode="oauth_callback",
                    owner="gateway-core.oauth",
                )
            )

    def test_validate_request_accepts_registered_content_type_with_charset(self) -> None:
        registry = GatewayRouteRegistry()
        registry.register(
            GatewayRouteRegistration(
                path="/webhooks/discord",
                methods=("POST",),
                auth_mode="adapter_webhook",
                owner="discord-adapter",
                content_types=("application/json",),
            )
        )

        route = registry.validate_request(
            path="/webhooks/discord",
            method="POST",
            content_type="application/json; charset=utf-8",
        )

        self.assertEqual(route.owner, "discord-adapter")

    def test_validate_request_rejects_wrong_content_type(self) -> None:
        registry = GatewayRouteRegistry()
        registry.register(
            GatewayRouteRegistration(
                path="/webhooks/whatsapp",
                methods=("POST",),
                auth_mode="adapter_webhook",
                owner="whatsapp-adapter",
                content_types=("application/json",),
            )
        )

        with self.assertRaisesRegex(ValueError, "rejects Content-Type"):
            registry.validate_request(
                path="/webhooks/whatsapp",
                method="POST",
                content_type="text/plain",
            )
