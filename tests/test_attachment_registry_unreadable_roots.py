from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from spark_intelligence.attachments import registry

from tests.test_support import SparkTestCase


class AttachmentRegistryUnreadableRootTests(SparkTestCase):
    @staticmethod
    def _chip(parent: Path, name: str) -> Path:
        chip = parent / name
        chip.mkdir(parents=True)
        (chip / "spark-chip.json").write_text("{}", encoding="utf-8")
        return chip

    def test_unreadable_installed_parent_does_not_hide_later_installed_chips(self) -> None:
        unreadable = self.home / "unreadable-chips"
        readable = self.home / "readable-chips"
        unreadable.mkdir()
        expected = self._chip(readable, "domain-chip-proof")
        real_iterdir = Path.iterdir

        def guarded_iterdir(path: Path):
            if path == unreadable:
                raise PermissionError("installed chip parent is unreadable")
            return real_iterdir(path)

        with patch.object(
            registry,
            "installed_chip_parent_candidates",
            return_value=[unreadable, readable],
        ), patch.object(Path, "iterdir", guarded_iterdir):
            roots, source = registry._resolve_chip_roots(self.config_manager)

        self.assertEqual(
            roots,
            [registry.canonical_chip_home(), expected],
        )
        self.assertEqual(source, "canonical+installed")

    def test_unreadable_legacy_desktop_returns_missing_instead_of_crashing(self) -> None:
        user_home = self.home / "user"
        desktop = user_home / "Desktop"
        desktop.mkdir(parents=True)
        real_iterdir = Path.iterdir

        def guarded_iterdir(path: Path):
            if path == desktop:
                raise PermissionError("Desktop is unreadable")
            return real_iterdir(path)

        with patch.object(registry, "installed_chip_parent_candidates", return_value=[]), patch(
            "pathlib.Path.home",
            return_value=user_home,
        ), patch.object(Path, "iterdir", guarded_iterdir):
            roots, source = registry._resolve_chip_roots(self.config_manager)

        self.assertEqual(roots, [registry.canonical_chip_home()])
        self.assertEqual(source, "canonical")

    def test_unresolvable_candidate_does_not_hide_other_valid_chip(self) -> None:
        parent = self.home / "chips"
        broken = self._chip(parent, "domain-chip-broken")
        expected = self._chip(parent, "domain-chip-proof")
        real_resolve = Path.resolve

        def guarded_resolve(path: Path, *args, **kwargs):
            if path == broken:
                raise OSError("candidate cannot be resolved")
            return real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", guarded_resolve):
            roots = registry._autodiscover_chip_roots([parent], set())

        self.assertEqual(roots, [expected])

    def test_iterdir_failure_preserves_domain_chip_glob_results(self) -> None:
        parent = self.home / "chips"
        expected = self._chip(parent, "domain-chip-proof")

        with patch.object(Path, "iterdir", side_effect=PermissionError("parent is unreadable")):
            roots = registry._autodiscover_chip_roots([parent], set())

        self.assertEqual(roots, [expected])

    def test_glob_failure_preserves_manifest_discovery(self) -> None:
        parent = self.home / "chips"
        expected = self._chip(parent, "custom-chip-proof")
        real_glob = Path.glob

        def guarded_glob(path: Path, pattern: str):
            if path == parent:
                raise PermissionError("glob is unavailable")
            return real_glob(path, pattern)

        with patch.object(Path, "glob", guarded_glob):
            roots = registry._autodiscover_chip_roots([parent], set())

        self.assertEqual(roots, [expected])
