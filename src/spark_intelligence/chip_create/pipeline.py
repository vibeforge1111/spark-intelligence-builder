from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ChipCreateResult:
    ok: bool
    chip_key: str | None = None
    chip_path: str | None = None
    brief: dict | None = None
    router_invokable: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_BRIEF_SYSTEM = (
    "You are a domain-chip brief parser for the Spark ecosystem. "
    "Given a natural-language request, produce a strict JSON brief for the chip scaffolder. "
    "Output ONLY the JSON object, no markdown, no commentary. "
    "Required fields: "
    "  domain_id (lowercase-kebab-case slug, e.g. 'supply-chain-risk'), "
    "  domain_name (title case human-readable name), "
    "  description (one sentence), "
    "  category (one of: research, creative, strategy, operations, analysis, automation, other), "
    "  primary_metric (snake_case name of the score the chip optimizes, e.g. 'risk_coverage_score'), "
    "  mutation_axes (array of 2 to 4 objects, each {name, values} where name is snake_case and values is 3-5 string options). "
    "Router fields (also required): "
    "  task_topics (array of 5-10 snake_case topic keys relevant to the chip's domain), "
    "  task_keywords (array of 10-20 single-word triggers that would appear in user messages when this chip should activate), "
    "  combine_with (array of existing chip keys this chip pairs with, or [] if none; common options: 'domain-chip-xcontent', 'startup-yc', 'spark-browser'). "
    "Do not include extra fields. Do not wrap in markdown fences."
)


def _default_chip_labs_root() -> Path:
    env = os.environ.get("CHIP_LABS_ROOT")
    if env:
        return Path(env)
    return Path("C:/Users/USER/Desktop/spark-domain-chip-labs")


def _default_output_dir() -> Path:
    env = os.environ.get("CHIP_CREATE_OUTPUT_DIR")
    if env:
        return Path(env)
    return Path("C:/Users/USER/Desktop")


def _ensure_chip_labs_importable(root: Path) -> None:
    src = str((root / "src").resolve())
    if src not in sys.path:
        sys.path.insert(0, src)


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_brief_via_llm(prompt: str, *, provider) -> dict:
    from spark_intelligence.llm.direct_provider import (
        DirectProviderRequest,
        execute_direct_provider_prompt,
    )

    req = DirectProviderRequest(
        provider_id=provider.provider_id,
        provider_kind=provider.provider_kind,
        auth_method=provider.auth_method,
        api_mode=provider.api_mode,
        base_url=provider.base_url,
        model=provider.default_model,
        secret_value=provider.secret_value,
    )
    result = execute_direct_provider_prompt(
        provider=req,
        system_prompt=_BRIEF_SYSTEM,
        user_prompt=f"User request:\n{prompt}\n\nReturn the JSON brief.",
    )
    raw = str(result.get("raw_response") or "")
    cleaned = _strip_code_fences(raw)
    return json.loads(cleaned)


def _validate_brief(brief: dict) -> list[str]:
    errors: list[str] = []
    for key in ("domain_id", "domain_name", "mutation_axes", "primary_metric"):
        if not brief.get(key):
            errors.append(f"missing {key}")
    axes = brief.get("mutation_axes")
    if isinstance(axes, list):
        for i, axis in enumerate(axes):
            if not isinstance(axis, dict):
                errors.append(f"mutation_axes[{i}] must be object")
                continue
            if not axis.get("name") or not isinstance(axis.get("values"), list):
                errors.append(f"mutation_axes[{i}] needs name + values list")
    return errors


def _normalize_commands(commands: Any) -> dict:
    if not isinstance(commands, dict):
        return {}
    result: dict[str, list[str]] = {}
    for name, value in commands.items():
        if isinstance(value, list):
            parts = [str(x) for x in value if str(x).strip()]
        elif isinstance(value, str):
            stripped = value.replace("--input {input}", "").replace("--output {output}", "")
            parts = [p for p in stripped.split() if p.strip()]
        else:
            continue
        while parts and parts[-1] in ("--input", "--output", "{input}", "{output}"):
            parts.pop()
        if parts:
            result[str(name)] = parts
    return result


def _patch_manifest_router_fields(manifest_path: Path, brief: dict, *, chip_key: str) -> None:
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not doc.get("chip_name"):
        doc["chip_name"] = chip_key
    normalized = _normalize_commands(doc.get("commands"))
    if normalized:
        doc["commands"] = normalized
    if not doc.get("io_protocol"):
        doc["io_protocol"] = "spark-hook-io.v1"
    for key in ("task_topics", "task_keywords", "combine_with"):
        val = brief.get(key)
        if isinstance(val, list):
            doc[key] = [str(x).strip() for x in val if str(x).strip()]
    manifest_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def create_chip_from_prompt(
    *,
    prompt: str,
    config_manager,
    state_db,
    output_dir: Path | None = None,
    chip_labs_root: Path | None = None,
) -> ChipCreateResult:
    warnings: list[str] = []
    chip_labs_root = chip_labs_root or _default_chip_labs_root()
    output_dir = output_dir or _default_output_dir()

    if not chip_labs_root.exists():
        return ChipCreateResult(ok=False, error=f"chip-labs root not found: {chip_labs_root}")

    _ensure_chip_labs_importable(chip_labs_root)
    try:
        from chip_labs.chip_factory.scaffold import scaffold_chip
    except Exception as exc:
        return ChipCreateResult(ok=False, error=f"chip_labs import failed: {exc}")

    # Resolve LLM provider using existing builder auth path
    try:
        from spark_intelligence.auth.runtime import resolve_runtime_provider
        provider = resolve_runtime_provider(
            config_manager=config_manager, state_db=state_db
        )
    except Exception as exc:
        return ChipCreateResult(ok=False, error=f"provider resolve failed: {exc}")

    if not getattr(provider, "secret_value", None):
        return ChipCreateResult(
            ok=False,
            error=f"no LLM secret for provider={getattr(provider,'provider_id','?')} auth_method={getattr(provider,'auth_method','?')}",
        )

    # 1) Parse brief
    try:
        brief = _parse_brief_via_llm(prompt, provider=provider)
    except Exception as exc:
        return ChipCreateResult(ok=False, error=f"brief parse failed: {exc}")

    validation_errors = _validate_brief(brief)
    if validation_errors:
        return ChipCreateResult(
            ok=False, brief=brief, error=f"brief validation: {validation_errors}"
        )

    # 2) Scaffold
    try:
        chip_dir = Path(scaffold_chip(brief, output_dir=output_dir))
    except Exception as exc:
        return ChipCreateResult(ok=False, brief=brief, error=f"scaffold failed: {exc}")

    manifest = chip_dir / "spark-chip.json"
    if not manifest.exists():
        return ChipCreateResult(
            ok=False,
            brief=brief,
            chip_path=str(chip_dir),
            error="scaffold produced no spark-chip.json",
        )

    # 3) Determine chip_key from directory name (the registry's actual key source)
    chip_key = chip_dir.name

    # 4) Patch manifest with chip_name + router fields
    try:
        _patch_manifest_router_fields(manifest, brief, chip_key=chip_key)
    except Exception as exc:
        warnings.append(f"router field patch failed: {exc}")

    # 5) Register + snapshot + pin + snapshot
    try:
        from spark_intelligence.attachments import (
            add_attachment_root,
            pin_chip,
            build_attachment_snapshot,
        )
        add_attachment_root(
            config_manager,
            target="chips",
            root=str(chip_dir),
        )
    except Exception as exc:
        warnings.append(f"add_attachment_root failed: {exc}")

    try:
        build_attachment_snapshot(config_manager)
    except Exception as exc:
        warnings.append(f"snapshot after add-root failed: {exc}")

    try:
        pin_chip(config_manager, chip_key=chip_key)
    except Exception as exc:
        warnings.append(f"pin_chip failed: {exc}")

    router_invokable = False
    try:
        snapshot = build_attachment_snapshot(config_manager)
        payload = (
            snapshot.to_payload() if hasattr(snapshot, "to_payload") else snapshot
        )
        records = (
            payload.get("records") if isinstance(payload, dict) else None
        ) or []
        for rec in records:
            if rec.get("key") != chip_key:
                continue
            mode = str(rec.get("attachment_mode") or "")
            has_signal = bool(rec.get("task_topics") or rec.get("task_keywords"))
            if mode in ("active", "pinned") and has_signal:
                router_invokable = True
            break
    except Exception as exc:
        warnings.append(f"snapshot failed: {exc}")

    return ChipCreateResult(
        ok=True,
        chip_key=chip_key,
        chip_path=str(chip_dir),
        brief=brief,
        router_invokable=router_invokable,
        warnings=warnings,
    )
