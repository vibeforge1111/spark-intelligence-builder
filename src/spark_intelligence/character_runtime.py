from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_spark_character_path() -> Path | None:
    """Make a starter-installed spark-character checkout importable.

    The Spark CLI exports SPARK_CHARACTER_ROOT for Builder and Telegram. In
    development installs that checkout may not also be installed into the same
    Python environment, so optional spark_character imports should first add
    the module's src directory when it exists.
    """
    raw_root = (os.environ.get("SPARK_CHARACTER_ROOT") or "").strip()
    if not raw_root:
        return None

    root = Path(raw_root).expanduser()
    for candidate in (root / "src", root):
        if not (candidate / "spark_character").exists():
            continue
        path_value = str(candidate)
        if path_value not in sys.path:
            sys.path.insert(0, path_value)
        return candidate
    return None
