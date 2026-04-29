from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB
from spark_intelligence.target_confirmation import evaluate_target_repo_confirmation


_QUALITY_QUERY_SIGNALS = (
    "build quality",
    "quality of this build",
    "quality of the build",
    "rate this build",
    "rate the build",
    "rate the quality",
    "how good is this build",
    "how would you rate",
    "review this build",
    "review the build",
    "is this build good",
)
_FALSE_POSITIVE_SIGNALS = (
    "memory quality",
    "reply quality",
    "communication quality",
    "quality evaluation",
)


@dataclass(frozen=True)
class BuildQualityEvidence:
    reply_text: str
    facts: dict[str, Any]


def looks_like_build_quality_review_query(message: str) -> bool:
    lowered = str(message or "").strip().casefold()
    if not lowered:
        return False
    if any(signal in lowered for signal in _FALSE_POSITIVE_SIGNALS):
        return False
    if any(signal in lowered for signal in _QUALITY_QUERY_SIGNALS):
        return "build" in lowered or "route" in lowered or "page" in lowered or "app" in lowered
    return False


def build_build_quality_review_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
) -> BuildQualityEvidence:
    target = evaluate_target_repo_confirmation(
        config_manager,
        task=f"review build quality: {user_message}",
        probe_git=False,
    ).to_payload()
    repo_key = str(target.get("selected_repo_key") or "").strip()
    repo_path = str(target.get("selected_repo_path") or "").strip()
    blockers = [str(item) for item in (target.get("blockers") or []) if str(item)]
    if not repo_key or not repo_path:
        blockers.append("Target repo is unknown; confirm which local repo/build should be reviewed.")

    repo = Path(repo_path) if repo_path else None
    git = _collect_git_evidence(repo) if repo is not None else {"status": "missing_target_repo"}
    route = _collect_route_evidence(repo, user_message=user_message) if repo is not None else {"status": "missing_target_repo"}
    tests = _collect_latest_test_evidence(state_db, repo_key=repo_key)
    demo = _collect_demo_evidence(state_db, repo_key=repo_key, route=route)

    missing = _missing_evidence(blockers=blockers, git=git, route=route, tests=tests, demo=demo)
    evidence_complete = not missing
    verdict = "ready_for_grounded_review" if evidence_complete else "not_ready_to_score"
    facts = {
        "status": "build_quality_review",
        "verdict": verdict,
        "evidence_complete": evidence_complete,
        "target_repo": repo_key or None,
        "target_repo_path": repo_path or None,
        "target_confirmation": target,
        "git": git,
        "route": route,
        "tests": tests,
        "demo": demo,
        "missing_evidence": missing,
        "blockers": blockers,
    }
    return BuildQualityEvidence(reply_text=_format_reply(facts), facts=facts)


def _collect_git_evidence(repo: Path) -> dict[str, Any]:
    if not repo.exists():
        return {"status": "missing_repo_path", "path": str(repo)}
    if not (repo / ".git").exists():
        return {"status": "not_git_repo", "path": str(repo)}
    status = _run_git(repo, ["status", "--short"])
    diff_stat = _run_git(repo, ["diff", "--stat"])
    staged_diff_stat = _run_git(repo, ["diff", "--cached", "--stat"])
    branch = _run_git(repo, ["branch", "--show-current"])
    status_lines = [line for line in status.splitlines() if line.strip()]
    return {
        "status": "dirty" if status_lines else "clean",
        "path": str(repo),
        "branch": branch.strip() or None,
        "changed_file_count": len(status_lines),
        "status_preview": status_lines[:8],
        "diff_stat": diff_stat.strip(),
        "staged_diff_stat": staged_diff_stat.strip(),
    }


def _run_git(repo: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return str(completed.stdout or "").strip()


def _collect_route_evidence(repo: Path, *, user_message: str) -> dict[str, Any]:
    requested_routes = _extract_routes(user_message)
    if not requested_routes:
        return {"status": "not_requested", "requested_routes": []}
    matches: list[dict[str, str]] = []
    missing: list[str] = []
    for route in requested_routes:
        route_matches = _find_route_files(repo, route)
        if route_matches:
            matches.extend({"route": route, "path": str(path)} for path in route_matches[:3])
        else:
            missing.append(route)
    return {
        "status": "ok" if matches and not missing else "missing_route",
        "requested_routes": requested_routes,
        "matches": matches,
        "missing_routes": missing,
    }


def _extract_routes(user_message: str) -> list[str]:
    routes: list[str] = []
    for match in re.findall(r"/[A-Za-z0-9][A-Za-z0-9_/-]*", str(user_message or "")):
        normalized = "/" + "/".join(part for part in match.split("/") if part)
        if normalized not in routes:
            routes.append(normalized)
    return routes[:5]


def _find_route_files(repo: Path, route: str) -> list[Path]:
    relative = Path(*[part for part in route.strip("/").split("/") if part])
    candidates = [
        repo / "src" / "routes" / relative / "+page.svelte",
        repo / "src" / "routes" / relative / "+page.ts",
        repo / "src" / "routes" / relative / "+page.tsx",
        repo / "app" / relative / "page.tsx",
        repo / "app" / relative / "page.ts",
        repo / "pages" / f"{relative.as_posix()}.tsx",
        repo / "pages" / f"{relative.as_posix()}.ts",
    ]
    return [path for path in candidates if path.exists()]


def _collect_latest_test_evidence(state_db: StateDB, *, repo_key: str) -> dict[str, Any]:
    keys = [
        f"build_quality:last_tests:{repo_key}",
        f"tests:last_result:{repo_key}",
        "build_quality:last_tests",
        "tests:last_result",
    ]
    with state_db.connect() as conn:
        for key in keys:
            row = conn.execute(
                "SELECT state_key, value, updated_at FROM runtime_state WHERE state_key = ? LIMIT 1",
                (key,),
            ).fetchone()
            if row is None:
                continue
            payload = _parse_json(row["value"])
            return {
                "status": str(payload.get("status") or payload.get("result") or "recorded"),
                "state_key": row["state_key"],
                "updated_at": row["updated_at"],
                "command": str(payload.get("command") or "").strip() or None,
                "summary": str(payload.get("summary") or payload.get("stdout") or "").strip()[:240],
            }
    return {"status": "missing", "state_key": None}


def _collect_demo_evidence(state_db: StateDB, *, repo_key: str, route: dict[str, Any]) -> dict[str, Any]:
    route_values = [str(item) for item in (route.get("requested_routes") or []) if str(item)]
    keys = [f"build_quality:last_demo:{repo_key}", f"demo:last_result:{repo_key}", "build_quality:last_demo"]
    with state_db.connect() as conn:
        for key in keys:
            row = conn.execute(
                "SELECT state_key, value, updated_at FROM runtime_state WHERE state_key = ? LIMIT 1",
                (key,),
            ).fetchone()
            if row is None:
                continue
            payload = _parse_json(row["value"])
            return {
                "status": str(payload.get("status") or payload.get("result") or "recorded"),
                "state_key": row["state_key"],
                "updated_at": row["updated_at"],
                "url": str(payload.get("url") or "").strip() or None,
                "summary": str(payload.get("summary") or "").strip()[:240],
            }
    return {"status": "missing" if route_values else "not_requested", "state_key": None}


def _missing_evidence(
    *,
    blockers: list[str],
    git: dict[str, Any],
    route: dict[str, Any],
    tests: dict[str, Any],
    demo: dict[str, Any],
) -> list[str]:
    missing: list[str] = []
    if blockers:
        missing.append("confirmed target repo")
    if str(git.get("status") or "") in {"missing_target_repo", "missing_repo_path", "not_git_repo"}:
        missing.append("git repo evidence")
    if str(route.get("status") or "") == "missing_route":
        missing.append("route exists")
    if str(tests.get("status") or "") in {"", "missing", "failed", "error"}:
        missing.append("passing test evidence")
    if str(demo.get("status") or "") in {"", "missing", "failed", "error"}:
        missing.append("route/demo evidence")
    return _dedupe(missing)


def _format_reply(facts: dict[str, Any]) -> str:
    repo_key = facts.get("target_repo") or "unknown"
    git = facts.get("git") or {}
    route = facts.get("route") or {}
    tests = facts.get("tests") or {}
    demo = facts.get("demo") or {}
    missing = [str(item) for item in (facts.get("missing_evidence") or []) if str(item)]
    lines = []
    if missing:
        lines.append("I should not rate the build yet. The grounded review evidence is incomplete.")
    else:
        lines.append("The grounded review evidence is complete enough to rate the build.")
    lines.extend(
        [
            "",
            "Evidence",
            f"- Target repo: {repo_key}",
            f"- Git state: {git.get('status') or 'unknown'} ({int(git.get('changed_file_count') or 0)} changed files)",
            f"- Route: {_route_summary(route)}",
            f"- Tests: {_test_summary(tests)}",
            f"- Demo: {_demo_summary(demo)}",
        ]
    )
    if missing:
        lines.extend(["", "Missing before a real rating"])
        lines.extend(f"- {item}" for item in missing)
        lines.extend(["", "Next: run/record tests and demo evidence, then ask for the review again."])
    else:
        lines.extend(["", "Next: rate against product fit, correctness, polish, reliability, and regression risk."])
    return "\n".join(lines)


def _route_summary(route: dict[str, Any]) -> str:
    status = str(route.get("status") or "unknown")
    if status == "not_requested":
        return "not requested"
    if status == "ok":
        routes = ", ".join(str(item.get("route")) for item in (route.get("matches") or [])[:3] if item.get("route"))
        return f"found {routes or 'requested route'}"
    missing = ", ".join(str(item) for item in (route.get("missing_routes") or []) if str(item))
    return f"{status} {missing}".strip()


def _test_summary(tests: dict[str, Any]) -> str:
    status = str(tests.get("status") or "unknown")
    command = str(tests.get("command") or "").strip()
    return f"{status}{f' via `{command}`' if command else ''}"


def _demo_summary(demo: dict[str, Any]) -> str:
    status = str(demo.get("status") or "unknown")
    url = str(demo.get("url") or "").strip()
    return f"{status}{f' at {url}' if url else ''}"


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        payload = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return {"summary": str(value or "")}
    return payload if isinstance(payload, dict) else {"summary": str(payload)}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
