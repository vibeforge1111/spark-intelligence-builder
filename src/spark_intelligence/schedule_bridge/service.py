from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any


_SPAWNER_URL = os.environ.get("SPAWNER_UI_URL") or "http://127.0.0.1:4174"

# Scope note: scheduler-related vocabulary the bot should route here.
# We match on any message that (a) asks about the scheduler surface or
# (b) asks a "what's running / what's set up / what's automated" type
# question. Kept permissive because this short-circuits before the LLM
# and is easy to override by just not asking about schedules.
_SCHEDULER_NOUNS = (
    r"schedules?|scheduled|cron\s*jobs?|cron|recurring|recurring\s+tasks?|"
    r"automations?|autoruns?|autoloops?|routines?|nightly|daily|weekly|"
    r"background\s+tasks?|repeating\s+tasks?|set\s*up\s+(?:tasks?|jobs?)"
)
_LIST_VERBS = (
    r"show|list|display|see|view|get|tell\s+me|what(?:\s+are|\s+is)?|whats?|"
    r"do\s+i\s+have|have\s+i\s+got|are\s+there|any|check|find|browse|"
    r"look\s+at|review|overview|status\s+of|summary\s+of"
)
_RUNTIME_QUALIFIERS = (
    r"right\s*now|currently|tonight|tomorrow|today|next|upcoming|planned|"
    r"running|enabled|active|live|pending|queued|set|configured"
)

_LIST_PATTERNS = (
    # "show my schedules", "list the scheduled jobs", "display cron jobs", etc.
    re.compile(rf"\b(?:{_LIST_VERBS})\b[^.\n]{{0,40}}\b(?:{_SCHEDULER_NOUNS})\b", re.IGNORECASE),
    # "what's scheduled", "whats on my schedule", "what are the schedules"
    re.compile(rf"\b(?:whats?|what\s+is|what\s+are)\b[^.\n]{{0,40}}\b(?:{_SCHEDULER_NOUNS}|running\s+automatically|running\s+in\s+background|automated)\b", re.IGNORECASE),
    # "schedules list", "cron status", "cron active"
    re.compile(rf"\b(?:{_SCHEDULER_NOUNS})\b[^.\n]{{0,25}}\b(?:{_RUNTIME_QUALIFIERS}|list|status)\b", re.IGNORECASE),
    # "do I have any schedules", "are there any cron jobs"
    re.compile(rf"\b(?:do\s+i\s+have|have\s+i|are\s+there|is\s+there|any)\b[^.\n]{{0,25}}\b(?:{_SCHEDULER_NOUNS})\b", re.IGNORECASE),
    # Bare "my schedules?", "schedules?", "my routines?" as a one-word query
    re.compile(r"^\s*(?:my\s+)?(?:schedules?|automations?|cron\s*jobs?|autoloops?|routines?|recurring\s+tasks?)\s*\??\s*$", re.IGNORECASE),
    # "what's running tonight", "anything fire tonight", "what's firing soon"
    re.compile(r"\b(?:what|anything)\b[^.\n]{0,20}\b(?:running|firing|fire|scheduled|queued)\b[^.\n]{0,20}\b(?:tonight|tomorrow|today|soon|next|later)\b", re.IGNORECASE),
    # "schedule board", "scheduler status", "show the scheduler"
    re.compile(r"\b(?:scheduler|schedule\s+board|schedule\s+page|schedule\s+panel)\b", re.IGNORECASE),
)


def detect_schedule_intent(message: str) -> dict | None:
    """Detect plain-language intent to list the scheduler surface.

    Returns {"action": "list"} on match, None otherwise. Errs on the side
    of matching since the short-circuit is cheap and easy to avoid
    (users who don't want schedules simply don't ask about them).
    """
    text = str(message or "").strip()
    if not text:
        return None
    for pat in _LIST_PATTERNS:
        if pat.search(text):
            return {"action": "list"}
    return None


_DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _format_12(h: int, m: int) -> str:
    hh = ((h + 11) % 12) + 1
    suffix = "AM" if h < 12 else "PM"
    return f"{hh} {suffix}" if m == 0 else f"{hh}:{m:02d} {suffix}"


def humanize_cron(cron: str) -> str:
    parts = cron.strip().split()
    if len(parts) != 5:
        return cron
    minute, hour, dom, month, dow = parts
    if hour == "*" and dom == "*" and month == "*" and dow == "*":
        if minute == "*":
            return "Every minute"
        m = re.match(r"^\*/(\d+)$", minute)
        if m:
            n = m.group(1)
            return f"Every {n} minute" + ("" if n == "1" else "s")
        if minute.isdigit():
            return f"At {minute} min past every hour"
    if dom == "*" and month == "*" and dow == "*":
        h = re.match(r"^\*/(\d+)$", hour)
        if h and minute.isdigit():
            n = h.group(1)
            return f"Every {n} hour" + ("" if n == "1" else "s") + f" at :{int(minute):02d}"
        if hour.isdigit() and minute.isdigit():
            return f"Daily at {_format_12(int(hour), int(minute))}"
    if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*" and re.match(r"^\d$", dow):
        return f"Every {_DOW[int(dow)]} at {_format_12(int(hour), int(minute))}"
    if minute.isdigit() and hour.isdigit() and dom.isdigit() and month == "*" and dow == "*":
        return f"Monthly on day {dom} at {_format_12(int(hour), int(minute))}"
    if minute.isdigit() and hour.isdigit() and dom.isdigit() and month.isdigit() and dow == "*":
        return f"Yearly on {_MON[int(month) - 1]} {dom} at {_format_12(int(hour), int(minute))}"
    return f"Custom: {cron}"


def _format_next_fire(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        now = datetime.now(timezone.utc)
        delta = (dt - now).total_seconds()
        local_str = local_dt.strftime("%a, %I:%M %p").lstrip("0").replace(" 0", " ")
        if delta <= 0:
            return f"{local_str} (due now)"
        if delta < 60:
            rel = f"{int(delta)}s"
        elif delta < 3600:
            rel = f"{int(delta // 60)}m"
        elif delta < 86_400:
            rel = f"{int(delta // 3600)}h"
        else:
            rel = f"{int(delta // 86_400)}d"
        return f"{local_str} (in {rel})"
    except Exception:
        return iso


def _human_summary(rec: dict[str, Any]) -> str:
    action = rec.get("action", "")
    payload = rec.get("payload") or {}
    if action == "mission":
        goal = str(payload.get("goal") or "(no goal)")
        return f'Run mission "{goal}"'
    chip = payload.get("chipKey") or "?"
    rounds = int(payload.get("rounds") or 1)
    plural = "" if rounds == 1 else "s"
    return f"Run {rounds} loop round{plural} on {chip}"


def format_schedule_list(schedules: list[dict[str, Any]]) -> str:
    """Conversational rendering of the scheduler surface.

    Reads like Spark answering a friend, not a terminal dumping fields.
    """
    if not schedules:
        return (
            "Nothing on the schedule right now. If you want me to run "
            "something on a cadence, say the word, or use /schedule to set it up."
        )
    n = len(schedules)
    opener = (
        f"Here's what I've got queued up ({n} active):"
        if n > 1
        else "Just one thing on the schedule:"
    )
    lines = [opener, ""]
    for rec in schedules:
        summary = _human_summary(rec)
        when = humanize_cron(str(rec.get("cron") or ""))
        next_fire = _format_next_fire(rec.get("nextFireAt"))
        fires = int(rec.get("fireCount", 0) or 0)
        last = rec.get("lastStatus")
        ran_bit = (
            " Hasn't fired yet." if fires == 0
            else f" Fired {fires} time{'' if fires == 1 else 's'} so far"
            + (f" (last result: {str(last)[:60]})." if last else ".")
        )
        lines.append(f"• {summary}")
        lines.append(f"  {when}, next run {next_fire}.{ran_bit}")
        lines.append(f"  (id {rec.get('id')} - delete with /schedules delete {rec.get('id')})")
        lines.append("")
    lines.append(
        "Want to add or kill one? Use /schedule to create, /schedules delete <id> to remove, "
        "or just ask me in plain English."
    )
    return "\n".join(lines).rstrip()


def fetch_schedules(spawner_url: str | None = None, *, timeout: float = 5.0) -> list[dict[str, Any]]:
    base = (spawner_url or _SPAWNER_URL).rstrip("/")
    req = urllib.request.Request(f"{base}/api/scheduled", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    records = data.get("schedules")
    return records if isinstance(records, list) else []


def format_schedule_list_from_spawner(spawner_url: str | None = None) -> str:
    schedules = fetch_schedules(spawner_url)
    return format_schedule_list(schedules)


# --- Delete intent + confirmation gate ----------------------------------

_DELETE_PATTERNS = (
    re.compile(r"\b(?:cancel|delete|kill|remove|stop|drop|disable|turn\s+off)\b.{0,40}\b(?:schedule|schedules?|cron|nightly|daily|weekly|autoloop|automation|routine|recurring(?:\s+task)?|scheduled\s+task|scheduled\s+job)\b", re.IGNORECASE),
    re.compile(r"\b(?:cancel|delete|kill|remove|stop|drop|disable|turn\s+off)\b.{0,40}\b(?:sched-[a-z0-9]+)\b", re.IGNORECASE),
    re.compile(r"\b(?:cancel|delete|kill|remove|stop)\b.{0,30}\b(?:my|the)\b.{0,30}\b(?:nightly|daily|weekly|morning|evening|3\s*am|3\s*pm|9\s*am|9\s*pm)\b", re.IGNORECASE),
)

_CONFIRM_YES_PATTERNS = (
    re.compile(r"^\s*yes\s*(?:cancel|delete|kill|remove|stop|do\s*it|confirm|go\s*ahead|please)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:confirm|confirmed|go\s*ahead|do\s*it|proceed)\s*$", re.IGNORECASE),
    re.compile(r"^\s*yes\s*$", re.IGNORECASE),
)

_CONFIRM_NO_PATTERNS = (
    re.compile(r"\b(?:never\s*mind|nevermind|cancel|abort|stop|wait|no|nope|skip|keep\s*it|leave\s*it)\b", re.IGNORECASE),
)


def detect_delete_intent(message: str) -> dict | None:
    """Detect intent to cancel/delete a schedule. Returns hints for matching."""
    text = str(message or "").strip()
    if not text:
        return None
    matched = False
    for pat in _DELETE_PATTERNS:
        if pat.search(text):
            matched = True
            break
    if not matched:
        return None
    hints: dict[str, Any] = {"raw": text}
    id_match = re.search(r"\b(sched-[a-z0-9]+)\b", text, re.IGNORECASE)
    if id_match:
        hints["schedule_id"] = id_match.group(1)
    for tod in ("nightly", "daily", "weekly", "morning", "evening"):
        if re.search(rf"\b{tod}\b", text, re.IGNORECASE):
            hints["time_of_day"] = tod
            break
    time_match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", text, re.IGNORECASE)
    if time_match:
        h = int(time_match.group(1))
        ampm = time_match.group(2).lower()
        if ampm == "pm" and h < 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
        hints["hour_24"] = h
    return {"action": "delete", "hints": hints}


def match_schedules(schedules: list[dict[str, Any]], hints: dict) -> list[dict[str, Any]]:
    """Filter schedules by delete-intent hints. Returns best matches."""
    if not schedules:
        return []
    sid = hints.get("schedule_id")
    if sid:
        return [s for s in schedules if s.get("id") == sid]
    candidates = list(schedules)
    hour = hints.get("hour_24")
    if hour is not None:
        filtered = []
        for s in candidates:
            parts = str(s.get("cron") or "").split()
            if len(parts) == 5 and parts[1].isdigit() and int(parts[1]) == hour:
                filtered.append(s)
        if filtered:
            candidates = filtered
    tod = hints.get("time_of_day")
    if tod in ("nightly", "morning", "evening", "daily"):
        # No precise filter; if only one schedule exists, treat it as the match.
        pass
    return candidates


# File-backed pending confirmations per external_user_id. TTL 5 minutes.
# Must persist across Python invocations because the telegram bot shells
# out to a fresh `python -m spark_intelligence.cli` per message.
from pathlib import Path as _Path

_PENDING_TTL_SECONDS = 300


def _pending_store_path() -> _Path:
    home_env = os.environ.get("SPARK_INTELLIGENCE_HOME")
    base = _Path(home_env) if home_env else _Path.home() / ".spark-intelligence"
    base.mkdir(parents=True, exist_ok=True)
    return base / "pending_confirmations.json"


def _load_pending() -> dict[str, Any]:
    p = _pending_store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pending(store: dict[str, Any]) -> None:
    import time as _time
    p = _pending_store_path()
    tmp = p.with_suffix(".json.tmp")
    now = _time.time()
    pruned = {k: v for k, v in store.items() if v.get("expires_at", 0) > now}
    tmp.write_text(json.dumps(pruned, indent=2), encoding="utf-8")
    tmp.replace(p)


def arm_pending_delete(user_id: str, schedule: dict[str, Any]) -> None:
    import time as _time
    store = _load_pending()
    store[str(user_id)] = {
        "schedule_id": schedule.get("id"),
        "cron": schedule.get("cron"),
        "payload": schedule.get("payload"),
        "action": schedule.get("action"),
        "expires_at": _time.time() + _PENDING_TTL_SECONDS,
    }
    _save_pending(store)


def peek_pending_delete(user_id: str) -> dict[str, Any] | None:
    import time as _time
    store = _load_pending()
    rec = store.get(str(user_id))
    if not rec:
        return None
    if rec.get("expires_at", 0) < _time.time():
        store.pop(str(user_id), None)
        _save_pending(store)
        return None
    return rec


def clear_pending_delete(user_id: str) -> None:
    store = _load_pending()
    if str(user_id) in store:
        store.pop(str(user_id), None)
        _save_pending(store)


def is_confirmation_yes(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _CONFIRM_YES_PATTERNS)


def is_confirmation_no(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _CONFIRM_NO_PATTERNS)


def format_delete_prompt(schedule: dict[str, Any]) -> str:
    payload = schedule.get("payload") or {}
    if schedule.get("action") == "mission":
        what = f'the mission "{payload.get("goal", "(no goal)")}"'
    else:
        what = f'the {payload.get("chipKey", "chip")} loop'
    when = humanize_cron(str(schedule.get("cron") or ""))
    sid = schedule.get("id")
    return (
        f'Kill {what} scheduled {when.lower()} ({sid})?\n'
        f'Say "yes cancel" to confirm, or "never mind" to keep it.'
    )


def format_delete_ambiguous(matches: list[dict[str, Any]]) -> str:
    lines = ["I found a few that could match. Which one do you mean?", ""]
    for s in matches[:5]:
        payload = s.get("payload") or {}
        tag = payload.get("goal") if s.get("action") == "mission" else payload.get("chipKey")
        when = humanize_cron(str(s.get("cron") or ""))
        lines.append(f'  {s.get("id")}: {tag} ({when})')
    lines.append("")
    lines.append("Reply with the id (e.g. cancel sched-abc123) or 'never mind' to back out.")
    return "\n".join(lines)


def format_delete_not_found(hints: dict) -> str:
    detail = ""
    if hints.get("schedule_id"):
        detail = f' (id {hints["schedule_id"]})'
    elif hints.get("time_of_day"):
        detail = f' ({hints["time_of_day"]})'
    elif hints.get("hour_24") is not None:
        detail = f' (at {hints["hour_24"]}:00)'
    return f"I don't see a matching schedule{detail}. Run 'show my schedules' to see what's active."


def delete_schedule_via_spawner(schedule_id: str, spawner_url: str | None = None, *, timeout: float = 5.0) -> bool:
    base = (spawner_url or _SPAWNER_URL).rstrip("/")
    req = urllib.request.Request(
        f"{base}/api/scheduled?id={urllib.parse.quote(schedule_id)}",
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return False


def format_delete_success(schedule: dict[str, Any]) -> str:
    payload = schedule.get("payload") or {}
    tag = payload.get("goal") if schedule.get("action") == "mission" else payload.get("chipKey")
    return f"Done. {tag} is off the schedule. If you want to bring it back, just tell me."


def format_delete_cancelled() -> str:
    return "Alright, keeping it as-is."
