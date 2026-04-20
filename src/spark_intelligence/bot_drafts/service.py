from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from spark_intelligence.state.db import StateDB


DRAFT_HANDLE_PATTERN = re.compile(r"\bD-([0-9a-f]{6,12})\b", re.IGNORECASE)
DRAFT_MIN_LENGTH = 800

_ITERATION_INTENT_PATTERN = re.compile(
    r"\b("
    r"optimi[sz]e\s+this|optimi[sz]e\s+that|optimi[sz]e\s+the|"
    r"iterate(?:\s+on)?(?:\s+this|\s+that|\s+the)?|"
    r"revise(?:\s+this|\s+that|\s+the)?|"
    r"improve(?:\s+this|\s+that|\s+the)?|"
    r"polish(?:\s+this|\s+that|\s+the)?|"
    r"refine(?:\s+this|\s+that|\s+the)?|"
    r"rewrite(?:\s+this|\s+that|\s+the)?|"
    r"sharpen(?:\s+this|\s+that|\s+the)?|"
    r"tighten(?:\s+this|\s+that|\s+the)?|"
    r"the\s+(?:last|previous|earlier)\s+(?:draft|article|thread|tweet|post|version)|"
    r"that\s+(?:draft|article|thread|tweet|post|version)|"
    r"my\s+(?:last|previous|earlier)\s+(?:draft|article|thread|tweet|post)"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class BotDraft:
    draft_id: str
    handle: str
    external_user_id: str
    channel_kind: str
    session_id: str | None
    content: str
    content_length: int
    chip_used: str | None
    topic_hint: str | None
    created_at: str

    def to_dict(self) -> dict:
        return {
            "draft_id": self.draft_id,
            "handle": self.handle,
            "external_user_id": self.external_user_id,
            "channel_kind": self.channel_kind,
            "session_id": self.session_id,
            "content_length": self.content_length,
            "chip_used": self.chip_used,
            "topic_hint": self.topic_hint,
            "created_at": self.created_at,
        }


def _short_handle_from_id(draft_id: str) -> str:
    raw = str(draft_id or "")
    if raw.startswith("D-"):
        raw = raw[2:]
    return f"D-{raw[:8]}"


def _topic_hint_from_content(content: str) -> str | None:
    text = (content or "").strip()
    if not text:
        return None
    first_line = text.splitlines()[0].strip()
    cleaned = re.sub(r"^[\(\[]?\d+/\d+[\)\]]?\s*", "", first_line)
    cleaned = re.sub(r"^[#>\-*\s]+", "", cleaned)
    if not cleaned:
        return None
    return cleaned[:140]


def save_draft(
    state_db: StateDB,
    *,
    external_user_id: str,
    channel_kind: str,
    content: str,
    session_id: str | None = None,
    chip_used: str | None = None,
    min_length: int = DRAFT_MIN_LENGTH,
) -> BotDraft | None:
    text = str(content or "")
    if len(text) < min_length:
        return None
    user = str(external_user_id or "").strip()
    channel = str(channel_kind or "").strip()
    if not user or not channel:
        return None
    draft_id = f"D-{uuid4().hex[:12]}"
    handle = _short_handle_from_id(draft_id)
    topic_hint = _topic_hint_from_content(text)
    created_at = datetime.now(UTC).isoformat()
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO bot_drafts(
                draft_id, external_user_id, channel_kind, session_id,
                content, content_length, chip_used, topic_hint, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (draft_id, user, channel, session_id, text, len(text),
             chip_used, topic_hint, created_at),
        )
    return BotDraft(
        draft_id=draft_id,
        handle=handle,
        external_user_id=user,
        channel_kind=channel,
        session_id=session_id,
        content=text,
        content_length=len(text),
        chip_used=chip_used,
        topic_hint=topic_hint,
        created_at=created_at,
    )


def update_draft_content(
    state_db: StateDB,
    *,
    draft_id: str,
    content: str,
    chip_used: str | None = None,
) -> bool:
    text = str(content or "")
    if not text or not draft_id:
        return False
    topic_hint = _topic_hint_from_content(text)
    updated_at = datetime.now(UTC).isoformat()
    with state_db.connect() as conn:
        cur = conn.execute(
            """
            UPDATE bot_drafts
            SET content = ?, content_length = ?, chip_used = COALESCE(?, chip_used),
                topic_hint = COALESCE(?, topic_hint), created_at = ?
            WHERE draft_id = ?
            """,
            (text, len(text), chip_used, topic_hint, updated_at, draft_id),
        )
        return cur.rowcount > 0


def list_recent_drafts(
    state_db: StateDB,
    *,
    external_user_id: str,
    channel_kind: str,
    limit: int = 20,
) -> list[BotDraft]:
    user = str(external_user_id or "").strip()
    channel = str(channel_kind or "").strip()
    if not user or not channel:
        return []
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT draft_id, external_user_id, channel_kind, session_id,
                   content, content_length, chip_used, topic_hint, created_at
            FROM bot_drafts
            WHERE external_user_id = ? AND channel_kind = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (user, channel, max(1, int(limit))),
        ).fetchall()
    return [_row_to_draft(row) for row in rows]


def find_draft_by_handle(
    state_db: StateDB,
    *,
    external_user_id: str,
    channel_kind: str,
    handle_or_id: str,
) -> BotDraft | None:
    raw = str(handle_or_id or "").strip()
    if not raw:
        return None
    if raw.startswith("D-"):
        raw_body = raw[2:]
    else:
        raw_body = raw
    user = str(external_user_id or "").strip()
    channel = str(channel_kind or "").strip()
    if not raw_body or not user or not channel:
        return None
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT draft_id, external_user_id, channel_kind, session_id,
                   content, content_length, chip_used, topic_hint, created_at
            FROM bot_drafts
            WHERE external_user_id = ? AND channel_kind = ?
              AND (draft_id = ? OR draft_id LIKE ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user, channel, f"D-{raw_body}", f"D-{raw_body}%"),
        ).fetchall()
    if not rows:
        return None
    return _row_to_draft(rows[0])


def detect_iteration_intent(message: str) -> dict | None:
    text = str(message or "").strip()
    if not text:
        return None
    handle_match = DRAFT_HANDLE_PATTERN.search(text)
    if handle_match:
        return {"matched_handle": handle_match.group(0).upper(), "trigger": "handle"}
    intent_match = _ITERATION_INTENT_PATTERN.search(text)
    if intent_match:
        return {"matched_handle": None, "trigger": intent_match.group(0).lower()}
    return None


def find_draft_for_iteration(
    state_db: StateDB,
    *,
    external_user_id: str,
    channel_kind: str,
    user_message: str,
) -> BotDraft | None:
    intent = detect_iteration_intent(user_message)
    if not intent:
        return None
    if intent.get("matched_handle"):
        return find_draft_by_handle(
            state_db,
            external_user_id=external_user_id,
            channel_kind=channel_kind,
            handle_or_id=intent["matched_handle"],
        )
    drafts = list_recent_drafts(
        state_db,
        external_user_id=external_user_id,
        channel_kind=channel_kind,
        limit=10,
    )
    if not drafts:
        return None
    topic_hint = _hint_from_user_message(user_message)
    if topic_hint:
        topic_tokens = set(re.findall(r"[a-z0-9]+", topic_hint.lower()))
        if topic_tokens:
            best: tuple[int, BotDraft] | None = None
            for draft in drafts:
                hay = (draft.topic_hint or "") + " " + (draft.content or "")[:600]
                hay_tokens = set(re.findall(r"[a-z0-9]+", hay.lower()))
                overlap = len(topic_tokens & hay_tokens)
                if overlap == 0:
                    continue
                if best is None or overlap > best[0]:
                    best = (overlap, draft)
            if best is not None:
                return best[1]
    return drafts[0]


def _hint_from_user_message(message: str) -> str:
    text = str(message or "").lower()
    match = re.search(
        r"(?:the|that|my|this)\s+([a-z0-9 \-]{3,80})\s+(?:article|thread|tweet|post|draft|version)",
        text,
    )
    if match:
        return match.group(1).strip()
    return ""


def _row_to_draft(row) -> BotDraft:
    draft_id = str(row["draft_id"])
    return BotDraft(
        draft_id=draft_id,
        handle=_short_handle_from_id(draft_id),
        external_user_id=str(row["external_user_id"]),
        channel_kind=str(row["channel_kind"]),
        session_id=str(row["session_id"]) if row["session_id"] else None,
        content=str(row["content"]),
        content_length=int(row["content_length"]),
        chip_used=str(row["chip_used"]) if row["chip_used"] else None,
        topic_hint=str(row["topic_hint"]) if row["topic_hint"] else None,
        created_at=str(row["created_at"]),
    )
