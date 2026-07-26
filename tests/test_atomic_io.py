from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from spark_intelligence.atomic_io import atomic_write_text


def test_atomic_write_text_replaces_with_one_complete_concurrent_payload(
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.json"
    first = '{"owner":"first","body":"' + ("a" * 10000) + '"}'
    second = '{"owner":"second","body":"' + ("b" * 10000) + '"}'

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(atomic_write_text, target, payload)
            for payload in (first, second)
        ]
        for future in futures:
            future.result()

    assert target.read_text(encoding="utf-8") in {first, second}
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []


def test_atomic_write_text_removes_temporary_file_when_replace_fails(
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact.json"
    target.write_text("old", encoding="utf-8")

    with patch("spark_intelligence.atomic_io.os.replace", side_effect=OSError("boom")):
        with pytest.raises(OSError, match="boom"):
            atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []
