from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable
from uuid import uuid4

from spark_intelligence.state.db import StateDB


@dataclass
class UserInstruction:
    instruction_id: str
    external_user_id: str
    channel_kind: str
    instruction_text: str
    source: str
    status: str
    created_at: str
    archived_at: str | None

    def to_dict(self) -> dict:
        return {
            "instruction_id": self.instruction_id,
            "external_user_id": self.external_user_id,
            "channel_kind": self.channel_kind,
            "instruction_text": self.instruction_text,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "archived_at": self.archived_at,
        }


_REMEMBER_PREFIXES = (
    re.compile(r"^\s*/remember\s+(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*remember\s+this\s*[:\-]\s*(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*remember\s+that\s*[:\-]?\s*(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*from\s+now\s+on\s*[:,\-]?\s*(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*going\s+forward\s*[:,\-]?\s*(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
)

_INLINE_DIRECTIVE = re.compile(
    r"\b(?:please\s+)?(?P<directive>always|never|stop)\s+(?P<body>[^.\n]{4,200})",
    re.IGNORECASE,
)

_FORGET_PREFIXES = (
    re.compile(r"^\s*/forget\s+(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*forget\s+(?:that\s+|the\s+)?(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*stop\s+remembering\s+(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*you\s+can\s+forget\s+(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
)


def detect_instruction_intent(message: str) -> dict | None:
    text = str(message or "").strip()
    if not text:
        return None
    for pattern in _FORGET_PREFIXES:
        match = pattern.match(text)
        if match:
            body = match.group("body").strip().rstrip(".!?")
            if body:
                return {"action": "forget", "instruction_text": body}
    for pattern in _REMEMBER_PREFIXES:
        match = pattern.match(text)
        if match:
            body = match.group("body").strip().rstrip(".")
            if body:
                return {"action": "remember", "instruction_text": body}
    inline = _INLINE_DIRECTIVE.search(text)
    if inline:
        directive = inline.group("directive").lower()
        body = inline.group("body").strip().rstrip(".!?,")
        if body and len(body.split()) >= 2:
            phrase = f"{directive} {body}".strip()
            return {"action": "remember", "instruction_text": phrase}
    return None


def add_instruction(
    state_db: StateDB,
    *,
    external_user_id: str,
    channel_kind: str,
    instruction_text: str,
    source: str = "explicit",
) -> UserInstruction:
    text = str(instruction_text or "").strip()
    if not text:
        raise ValueError("instruction_text is required")
    user = str(external_user_id or "").strip()
    channel = str(channel_kind or "").strip()
    if not user or not channel:
        raise ValueError("external_user_id and channel_kind are required")
    instruction_id = f"inst-{uuid4().hex}"
    created_at = datetime.now(UTC).isoformat()
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_instructions(
                instruction_id, external_user_id, channel_kind,
                instruction_text, source, status, created_at, archived_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, NULL)
            """,
            (instruction_id, user, channel, text, source, created_at),
        )
    return UserInstruction(
        instruction_id=instruction_id,
        external_user_id=user,
        channel_kind=channel,
        instruction_text=text,
        source=source,
        status="active",
        created_at=created_at,
        archived_at=None,
    )


def list_active_instructions(
    state_db: StateDB,
    *,
    external_user_id: str,
    channel_kind: str,
    limit: int = 30,
) -> list[UserInstruction]:
    user = str(external_user_id or "").strip()
    channel = str(channel_kind or "").strip()
    if not user or not channel:
        return []
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT instruction_id, external_user_id, channel_kind, instruction_text,
                   source, status, created_at, archived_at
            FROM user_instructions
            WHERE external_user_id = ? AND channel_kind = ? AND status = 'active'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (user, channel, max(1, int(limit))),
        ).fetchall()
    return [
        UserInstruction(
            instruction_id=str(row["instruction_id"]),
            external_user_id=str(row["external_user_id"]),
            channel_kind=str(row["channel_kind"]),
            instruction_text=str(row["instruction_text"]),
            source=str(row["source"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            archived_at=str(row["archived_at"]) if row["archived_at"] else None,
        )
        for row in rows
    ]


def archive_instruction(state_db: StateDB, *, instruction_id: str) -> bool:
    if not instruction_id:
        return False
    archived_at = datetime.now(UTC).isoformat()
    with state_db.connect() as conn:
        cur = conn.execute(
            """
            UPDATE user_instructions
            SET status = 'archived', archived_at = ?
            WHERE instruction_id = ? AND status = 'active'
            """,
            (archived_at, instruction_id),
        )
        return cur.rowcount > 0


def matching_instructions_to_archive(
    state_db: StateDB,
    *,
    external_user_id: str,
    channel_kind: str,
    needle: str,
    limit: int = 5,
) -> list[UserInstruction]:
    needle_norm = str(needle or "").strip().lower()
    if not needle_norm:
        return []
    candidates = list_active_instructions(
        state_db,
        external_user_id=external_user_id,
        channel_kind=channel_kind,
        limit=200,
    )
    needle_tokens = set(re.findall(r"[a-z0-9]+", needle_norm))
    if not needle_tokens:
        return []
    scored: list[tuple[int, UserInstruction]] = []
    for inst in candidates:
        text_tokens = set(re.findall(r"[a-z0-9]+", inst.instruction_text.lower()))
        overlap = len(needle_tokens & text_tokens)
        if overlap == 0:
            continue
        scored.append((overlap, inst))
    scored.sort(key=lambda pair: -pair[0])
    return [inst for _, inst in scored[:limit]]
