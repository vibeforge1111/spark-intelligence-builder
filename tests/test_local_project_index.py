from __future__ import annotations

from spark_intelligence.local_project_index import build_local_project_index

from tests.test_support import SparkTestCase


class LocalProjectIndexTests(SparkTestCase):
    def test_build_local_project_index_reads_configured_project_roots(self) -> None:
        spawner_root = self.home / "spawner-ui"
        spawner_root.mkdir(parents=True)
        (spawner_root / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
        (spawner_root / "src").mkdir()
        self.config_manager.set_path(
            "spark.local_projects.records",
            {
                "spawner-ui": {
                    "path": str(spawner_root),
                    "label": "Spawner UI",
                    "components": ["mission_control", "memory_quality_dashboard"],
                    "capabilities": ["operator_dashboard"],
                    "aliases": ["spawner", "mission control"],
                }
            },
        )

        payload = build_local_project_index(self.config_manager, probe_git=False).to_payload()
        records = {record["key"]: record for record in payload["records"]}

        self.assertIn("spawner-ui", records)
        record = records["spawner-ui"]
        self.assertTrue(record["exists"])
        self.assertEqual(record["label"], "Spawner UI")
        self.assertIn("mission_control", record["components"])
        self.assertIn("memory_quality_dashboard", record["components"])
        self.assertIn("node_app", record["components"])
        self.assertIn("operator_dashboard", record["capabilities"])
        self.assertIn("frontend_or_node_runtime", record["capabilities"])
        self.assertIn("mission control", record["aliases"])
        self.assertEqual(record["owner_system"], "spark_spawner")

    def test_build_local_project_index_discovers_installed_spark_modules(self) -> None:
        installed_spawner = self.home / ".spark" / "modules" / "spawner-ui" / "source"
        installed_spawner.mkdir(parents=True)
        (installed_spawner / "package.json").write_text("{}", encoding="utf-8")
        self.config_manager.set_path("spark.local_projects.include_attachment_repos", False)
        self.config_manager.set_path("spark.local_projects.include_known_spark_repos", True)

        payload = build_local_project_index(self.config_manager, probe_git=False).to_payload()
        records = {record["key"]: record for record in payload["records"]}

        self.assertIn("spawner-ui", records)
        self.assertEqual(records["spawner-ui"]["path"], str(installed_spawner.resolve()))
        self.assertEqual(records["spawner-ui"]["source"], "installed_spark_module")

    def test_build_local_project_index_discovers_memory_quality_dashboard_as_separate_repo(self) -> None:
        dashboard_root = self.home / "spark-memory-quality-dashboard"
        dashboard_root.mkdir(parents=True)
        (dashboard_root / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
        (dashboard_root / "src").mkdir()
        (dashboard_root / "tests").mkdir()
        self.config_manager.set_path("spark.local_projects.include_attachment_repos", False)
        self.config_manager.set_path("spark.local_projects.include_known_spark_repos", True)

        payload = build_local_project_index(self.config_manager, probe_git=False).to_payload()
        records = {record["key"]: record for record in payload["records"]}

        self.assertIn("spark-memory-quality-dashboard", records)
        record = records["spark-memory-quality-dashboard"]
        self.assertIn(record["source"], {"known_spark_repo", "known_desktop_repo"})
        self.assertIn("memory_quality_dashboard", record["components"])
        self.assertIn("memory_quality_monitoring", record["capabilities"])
        self.assertEqual(record["owner_system"], "spark_memory_quality")
