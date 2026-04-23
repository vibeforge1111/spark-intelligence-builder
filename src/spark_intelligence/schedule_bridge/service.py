from __future__ import annotations

import json
import os
import re
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
    # Bare "my schedules?", "schedules?" as a one-word query
    re.compile(r"^\s*(?:my\s+)?(?:schedules?|automations?|cron\s*jobs?|autoloops?)\s*\??\s*$", re.IGNORECASE),
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
