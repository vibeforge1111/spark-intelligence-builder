from __future__ import annotations

from spark_intelligence.config.loader import ConfigManager

MEMORY_ENABLED_DEFAULT = False
MEMORY_SHADOW_MODE_DEFAULT = True


def memory_enabled(config_manager: ConfigManager) -> bool:
    return bool(config_manager.get_path("spark.memory.enabled", default=MEMORY_ENABLED_DEFAULT))


def memory_shadow_mode(config_manager: ConfigManager) -> bool:
    return bool(config_manager.get_path("spark.memory.shadow_mode", default=MEMORY_SHADOW_MODE_DEFAULT))
