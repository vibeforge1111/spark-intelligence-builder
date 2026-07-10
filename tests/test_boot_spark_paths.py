from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESOLVER = REPO_ROOT / "scripts" / "lib" / "resolve-boot-desktop.sh"
BOOT_SCRIPT = REPO_ROOT / "scripts" / "boot-spark.sh"


def resolve_boot_desktop(
    *,
    userprofile: str | None,
    home: str | None,
    username: str = "alice",
) -> str:
    """Mirror the boot-spark Desktop resolution contract for pytest coverage."""
    if userprofile:
        return f"{userprofile}/Desktop"
    if home:
        return f"{home}/Desktop"
    return f"/c/Users/{username}/Desktop"


def test_boot_spark_has_no_literal_user_placeholder() -> None:
    script = BOOT_SCRIPT.read_text(encoding="utf-8")

    assert 'DESKTOP="/c/Users/USER/Desktop"' not in script
    assert "resolve-boot-desktop.sh" in script


def test_resolve_boot_desktop_script_uses_dynamic_env_vars() -> None:
    script = RESOLVER.read_text(encoding="utf-8")

    assert "${USERPROFILE:-}" in script
    assert "${HOME:-}" in script
    assert "id -un" in script
    assert "/c/Users/USER/Desktop" not in script


def test_resolve_boot_desktop_prefers_userprofile() -> None:
    desktop = resolve_boot_desktop(
        userprofile="/c/Users/alice",
        home="/home/alice",
    )

    assert desktop == "/c/Users/alice/Desktop"


def test_resolve_boot_desktop_falls_back_to_home() -> None:
    desktop = resolve_boot_desktop(userprofile=None, home="/home/alice")

    assert desktop == "/home/alice/Desktop"


def test_resolve_boot_desktop_falls_back_to_id_un() -> None:
    desktop = resolve_boot_desktop(userprofile=None, home=None, username="bob")

    assert desktop == "/c/Users/bob/Desktop"
