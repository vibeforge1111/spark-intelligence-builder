from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from spark_intelligence.harness_contract import verify_governor_tool_authority


class ChipCreateAuthorityError(RuntimeError):
    def __init__(self, message: str, verification: dict[str, Any]):
        super().__init__(message)
        self.verification = verification


@dataclass
class ChipCreateResult:
    ok: bool
    chip_key: str | None = None
    chip_path: str | None = None
    brief: dict | None = None
    router_invokable: bool = False
    governor_verification: dict[str, Any] | None = None
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
    return Path.cwd() / "spark-domain-chip-labs"


def _default_output_dir() -> Path:
    env = os.environ.get("CHIP_CREATE_OUTPUT_DIR")
    if env:
        return Path(env)
    return Path.cwd()


def _ensure_chip_labs_importable(root: Path) -> None:
    src = str((root / "src").resolve())
    if src not in sys.path:
        sys.path.insert(0, src)


def _ensure_chip_labs_scaffold_present(root: Path) -> None:
    expected = root / "src" / "chip_labs" / "chip_factory" / "scaffold.py"
    if not expected.exists():
        raise ModuleNotFoundError(
            f"chip_labs.chip_factory.scaffold not found under {root / 'src'}"
        )


def _evict_stale_chip_labs_modules(root: Path) -> None:
    expected_src = (root / "src").resolve()
    for name, module in list(sys.modules.items()):
        if name != "chip_labs" and not name.startswith("chip_labs."):
            continue
        module_file = getattr(module, "__file__", None)
        if not module_file:
            del sys.modules[name]
            continue
        try:
            Path(module_file).resolve().relative_to(expected_src)
        except ValueError:
            del sys.modules[name]


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


def _patch_manifest_command_modules(manifest_path: Path, chip_dir: Path) -> None:
    """Rewrite command entries like ['python','-m','<pkg>',...] to
    ['python','-m','<pkg>.cli',...] when <pkg>/cli.py exists and there
    is no __main__.py. The scaffolder emits the bare module path but
    the generated package has no __main__, so `python -m <pkg>` fails.
    """
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    commands = doc.get("commands")
    if not isinstance(commands, dict):
        return
    src_dir = chip_dir / "src"
    if not src_dir.exists():
        return
    changed = False
    for hook, parts in list(commands.items()):
        if not isinstance(parts, list) or len(parts) < 3:
            continue
        if parts[0] != "python" or parts[1] != "-m":
            continue
        mod = parts[2]
        if not mod or "." in mod:
            continue
        pkg_dir = src_dir / mod
        has_main = (pkg_dir / "__main__.py").exists()
        has_cli = (pkg_dir / "cli.py").exists()
        if has_cli and not has_main:
            parts[2] = f"{mod}.cli"
            changed = True
    if changed:
        manifest_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _patch_generated_cli(chip_dir: Path, chip_labs_root: Path) -> None:
    """Rewrite the scaffolded cli.py so it can run as a standalone module.

    The scaffolder emits `from ..lab_hooks import (...)`, which breaks
    when the chip is scaffolded outside the chip_labs package. We
    rewrite it to an absolute import and prepend a sys.path shim that
    locates chip_labs via env (CHIP_LABS_SRC) or the known default.
    """
    chip_labs_src = (chip_labs_root / "src").resolve()
    src_dir = chip_dir / "src"
    if not src_dir.exists():
        return
    for cli_path in src_dir.glob("*/cli.py"):
        text = cli_path.read_text(encoding="utf-8")
        if "from ..lab_hooks" not in text:
            continue
        shim = (
            "import os as _os\n"
            "import sys as _sys\n"
            f"_CHIP_LABS_SRC = _os.environ.get('CHIP_LABS_SRC') or r'{chip_labs_src}'\n"
            "if _CHIP_LABS_SRC and _CHIP_LABS_SRC not in _sys.path:\n"
            "    _sys.path.insert(0, _CHIP_LABS_SRC)\n"
        )
        patched = text.replace(
            "from ..lab_hooks import",
            "from chip_labs.lab_hooks import",
        )
        # Insert shim after the module docstring (if any) and `from __future__` line.
        marker = "from __future__ import annotations\n"
        if marker in patched:
            patched = patched.replace(marker, marker + "\n" + shim + "\n", 1)
        else:
            patched = shim + "\n" + patched
        cli_path.write_text(patched, encoding="utf-8")


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


def _verify_chip_create_governor_authority(
    governor_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    verification = verify_governor_tool_authority(
        governor_decision,
        tool_name="chip.create",
        owner_system="spark-intelligence-builder",
        mutation_class="creates_chip",
        require_pre_execution_ledger=True,
    )
    if verification.get("allowed") is True:
        return verification
    reasons = [str(reason) for reason in verification.get("reason_codes") or [] if str(reason)]
    reason_text = ", ".join(reasons) if reasons else "governor_consumer_verification_failed"
    raise ChipCreateAuthorityError(
        "Chip creation requires Harness Core Governor authority for "
        f"spark-intelligence-builder:chip.create. Reason: {reason_text}.",
        verification,
    )


def create_chip_from_prompt(
    *,
    prompt: str,
    config_manager,
    state_db,
    output_dir: Path | None = None,
    chip_labs_root: Path | None = None,
    governor_decision: dict[str, Any] | None = None,
) -> ChipCreateResult:
    warnings: list[str] = []
    chip_labs_root = chip_labs_root or _default_chip_labs_root()
    output_dir = output_dir or _default_output_dir()

    if not isinstance(prompt, str) or not prompt.strip():
        # Guard against an empty or whitespace-only prompt before any paid
        # LLM dispatch. _parse_brief_via_llm would otherwise forward the
        # full _BRIEF_SYSTEM directive plus an empty user_prompt template to
        # the configured provider, burning tokens on a request that cannot
        # produce a valid brief (the LLM has nothing to parse). The brief
        # validator would then reject the result anyway, so the call is
        # guaranteed-redundant. Closing this gap eliminates the wasted
        # provider round-trip on empty input.
        return ChipCreateResult(
            ok=False,
            error="empty prompt: chip-create requires a non-empty natural-language description",
        )

    try:
        governor_verification = _verify_chip_create_governor_authority(governor_decision)
    except ChipCreateAuthorityError as exc:
        return ChipCreateResult(
            ok=False,
            governor_verification=exc.verification,
            error=str(exc),
        )

    if not chip_labs_root.exists():
        return ChipCreateResult(
            ok=False,
            governor_verification=governor_verification,
            error=f"chip-labs root not found: {chip_labs_root}",
        )

    try:
        _ensure_chip_labs_scaffold_present(chip_labs_root)
        _ensure_chip_labs_importable(chip_labs_root)
        _evict_stale_chip_labs_modules(chip_labs_root)
        from chip_labs.chip_factory.scaffold import scaffold_chip
    except Exception as exc:
        return ChipCreateResult(
            ok=False,
            governor_verification=governor_verification,
            error=f"chip_labs import failed: {exc}",
        )

    # Resolve LLM provider using existing builder auth path
    try:
        from spark_intelligence.auth.runtime import resolve_runtime_provider
        provider = resolve_runtime_provider(
            config_manager=config_manager, state_db=state_db
        )
    except Exception as exc:
        return ChipCreateResult(
            ok=False,
            governor_verification=governor_verification,
            error=f"provider resolve failed: {exc}",
        )

    if not getattr(provider, "secret_value", None):
        return ChipCreateResult(
            ok=False,
            governor_verification=governor_verification,
            error=f"no LLM secret for provider={getattr(provider,'provider_id','?')} auth_method={getattr(provider,'auth_method','?')}",
        )

    # 1) Parse brief
    try:
        brief = _parse_brief_via_llm(prompt, provider=provider)
    except Exception as exc:
        return ChipCreateResult(
            ok=False,
            governor_verification=governor_verification,
            error=f"brief parse failed: {exc}",
        )

    validation_errors = _validate_brief(brief)
    if validation_errors:
        return ChipCreateResult(
            ok=False,
            brief=brief,
            governor_verification=governor_verification,
            error=f"brief validation: {validation_errors}",
        )

    # 2) Scaffold
    try:
        chip_dir = Path(scaffold_chip(brief, output_dir=output_dir))
    except Exception as exc:
        return ChipCreateResult(
            ok=False,
            brief=brief,
            governor_verification=governor_verification,
            error=f"scaffold failed: {exc}",
        )

    manifest = chip_dir / "spark-chip.json"
    if not manifest.exists():
        return ChipCreateResult(
            ok=False,
            brief=brief,
            chip_path=str(chip_dir),
            governor_verification=governor_verification,
            error="scaffold produced no spark-chip.json",
        )

    # 3) Determine chip_key from directory name (the registry's actual key source)
    chip_key = chip_dir.name

    # 4) Patch manifest with chip_name + router fields
    try:
        _patch_manifest_router_fields(manifest, brief, chip_key=chip_key)
    except Exception as exc:
        warnings.append(f"router field patch failed: {exc}")

    # 4b) Patch generated cli.py so it can import chip_labs.lab_hooks without
    #     requiring editable install or living inside chip_labs's tree.
    try:
        _patch_generated_cli(chip_dir, chip_labs_root)
    except Exception as exc:
        warnings.append(f"cli.py import patch failed: {exc}")

    # 4c) Rewrite manifest commands to use <pkg>.cli when the scaffolded
    #     package has no __main__.py (the default).
    try:
        _patch_manifest_command_modules(manifest, chip_dir)
    except Exception as exc:
        warnings.append(f"manifest command module patch failed: {exc}")

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
        governor_verification=governor_verification,
        warnings=warnings,
    )
