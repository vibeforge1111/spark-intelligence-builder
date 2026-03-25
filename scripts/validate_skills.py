#!/usr/bin/env python3
"""
Lightweight validator for Spark Intelligence repo skills.

This checks that each skill:
- has the expected structural sections
- references files that actually exist
- keeps relative references resolvable from the skill folder or repo root
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"

REQUIRED_SKILL_SECTIONS = [
    "## Read First",
    "## Core Doctrine",
    "## Workflow",
    "## Required Outputs",
    "## Default Deliverable",
]

REFERENCE_LINE_RE = re.compile(r"`([^`]+)`")


def iter_skill_dirs() -> list[Path]:
    return sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir())


def resolve_reference(skill_dir: Path, ref: str) -> Path | None:
    candidates = [
        skill_dir / ref,
        REPO_ROOT / ref,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def collect_references(skill_text: str) -> list[str]:
    refs: list[str] = []
    for line in skill_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        refs.extend(REFERENCE_LINE_RE.findall(stripped))
    return refs


def validate_skill(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.exists():
        return [f"{skill_dir.name}: missing SKILL.md"]

    skill_text = skill_file.read_text(encoding="utf-8")

    for section in REQUIRED_SKILL_SECTIONS:
        if section not in skill_text:
            errors.append(f"{skill_dir.name}: missing required section {section}")

    references = collect_references(skill_text)
    if not references:
        errors.append(f"{skill_dir.name}: no referenced docs found in bullet lists")

    for ref in references:
        resolved = resolve_reference(skill_dir, ref)
        if resolved is None:
            errors.append(f"{skill_dir.name}: missing referenced path `{ref}`")

    return errors


def main() -> int:
    if not SKILLS_DIR.exists():
        print("skills directory not found", file=sys.stderr)
        return 1

    errors: list[str] = []
    for skill_dir in iter_skill_dirs():
        errors.extend(validate_skill(skill_dir))

    if errors:
        print("Skill validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Skill validation passed.")
    print(f"Validated {len(iter_skill_dirs())} skills in {SKILLS_DIR}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
