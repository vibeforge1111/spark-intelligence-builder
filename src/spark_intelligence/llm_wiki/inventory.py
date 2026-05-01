from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.llm_wiki.bootstrap import bootstrap_llm_wiki
from spark_intelligence.llm_wiki.compile_system import compile_system_wiki
from spark_intelligence.llm_wiki.status import BOOTSTRAP_WIKI_FILES, SYSTEM_COMPILE_WIKI_FILES
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class LlmWikiInventoryResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        pages = [page for page in self.payload.get("pages") or [] if isinstance(page, dict)]
        lines = [
            "Spark LLM wiki inventory",
            f"- output_dir: {self.output_dir}",
            f"- pages: {self.payload.get('page_count', 0)}",
            f"- expected_missing: {len(self.payload.get('missing_expected_files') or [])}",
            f"- authority: {self.payload.get('authority') or 'supporting_not_authoritative'}",
        ]
        if self.payload.get("refreshed"):
            lines.append(f"- refreshed: yes ({self.payload.get('refreshed_file_count', 0)} generated file(s))")
        if pages:
            lines.append("Pages")
            for page in pages[:12]:
                title = str(page.get("title") or page.get("path") or "").strip()
                relative_path = str(page.get("path") or "").strip()
                summary = str(page.get("summary") or "").strip()
                lines.append(f"- {relative_path}: {title}")
                if summary:
                    lines.append(f"  {summary[:180]}")
        missing = [str(item) for item in self.payload.get("missing_expected_files") or [] if str(item).strip()]
        if missing:
            lines.append("Missing expected pages")
            lines.extend(f"- {item}" for item in missing[:12])
        return "\n".join(lines)


def build_llm_wiki_inventory(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
    refresh: bool = False,
    limit: int = 40,
) -> LlmWikiInventoryResult:
    root = (Path(output_dir) if output_dir else config_manager.paths.home / "wiki").resolve(strict=False)
    refreshed_files: tuple[str, ...] = ()
    if refresh:
        bootstrap_llm_wiki(config_manager=config_manager, output_dir=root)
        compile_result = compile_system_wiki(config_manager=config_manager, state_db=state_db, output_dir=root)
        refreshed_files = compile_result.generated_files

    markdown_files = sorted(path for path in root.rglob("*.md") if path.is_file()) if root.exists() else []
    pages = [_page_payload(root, path) for path in markdown_files]
    expected_files = tuple(dict.fromkeys((*BOOTSTRAP_WIKI_FILES, *SYSTEM_COMPILE_WIKI_FILES)))
    missing_expected = [relative for relative in expected_files if not (root / relative).exists()]
    payload = {
        "output_dir": str(root),
        "checked_at": _utc_timestamp(),
        "exists": root.exists(),
        "page_count": len(pages),
        "pages": pages[: max(0, limit)],
        "returned_page_count": min(len(pages), max(0, limit)),
        "section_counts": _section_counts(pages),
        "expected_files": list(expected_files),
        "missing_expected_files": missing_expected,
        "bootstrap_page_count": sum(1 for page in pages if page.get("path") in BOOTSTRAP_WIKI_FILES),
        "system_compile_page_count": sum(1 for page in pages if page.get("path") in SYSTEM_COMPILE_WIKI_FILES),
        "refreshed": refresh,
        "refreshed_files": list(refreshed_files),
        "refreshed_file_count": len(refreshed_files),
        "authority": "supporting_not_authoritative",
        "runtime_hook": "hybrid_memory_retrieve.wiki_packets",
    }
    return LlmWikiInventoryResult(output_dir=root, payload=payload)


def _page_payload(root: Path, path: Path) -> dict[str, Any]:
    relative_path = path.relative_to(root).as_posix()
    content = path.read_text(encoding="utf-8", errors="replace")
    frontmatter = _frontmatter(content)
    return {
        "path": relative_path,
        "title": str(frontmatter.get("title") or _heading_title(content) or path.stem).strip(),
        "summary": str(frontmatter.get("summary") or "").strip(),
        "tags": _list_value(frontmatter.get("tags")),
        "type": str(frontmatter.get("type") or "").strip(),
        "status": str(frontmatter.get("status") or "").strip(),
        "authority": str(frontmatter.get("authority") or "supporting_not_authoritative").strip(),
        "freshness": str(frontmatter.get("freshness") or "").strip(),
        "source_class": str(frontmatter.get("source_class") or "").strip(),
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat(),
        "byte_count": path.stat().st_size,
    }


def _frontmatter(content: str) -> dict[str, Any]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    parsed: dict[str, Any] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        if value.startswith("[") and value.endswith("]"):
            parsed[key] = [item.strip().strip('"').strip("'") for item in value[1:-1].split(",") if item.strip()]
        else:
            parsed[key] = value
    return parsed


def _heading_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _section_counts(pages: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for page in pages:
        relative_path = str(page.get("path") or "")
        section = relative_path.split("/", 1)[0] if "/" in relative_path else "root"
        counts[section] = counts.get(section, 0) + 1
    return dict(sorted(counts.items()))


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
