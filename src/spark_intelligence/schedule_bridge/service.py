from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any


_SPAWNER_URL = os.environ.get("SPAWNER_UI_URL") or "http://127.0.0.1:4174"

# Natural-language intent for scheduler list queries. Deliberately narrow to
# avoid false positives — we only short-circuit if the user is clearly asking
# about their scheduled tasks.
_LIST_PATTERNS = (
    re.compile(r"\b(?:show|list|display|see|view|get)\b.{0,40}\b(?:schedule|schedules|scheduled|cron|recurring)\b", re.IGNORECASE),
    re.compile(r"\b(?:whats?|what\s+is|what\s+are|what\s+have)\b.{0,30}\b(?:schedule|schedules|scheduled)\b", re.IGNORECASE),
    re.compile(r"\b(?:schedules?|cron\s*jobs?)\b.{0,20}\b(?:list|status|active|enabled|right\s*now|do\s+i\s+have)\b", re.IGNORECASE),
    re.compile(r"\b(?:do\s+i\s+have|are\s+there|any)\b.{0,20}\b(?:schedules?|scheduled|cron)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:my\s+)?schedules?\s*\??\s*$", re.IGNORECASE),
)


def detect_schedule_intent(message: str) -> dict | None:
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
    if not schedules:
        return "No schedules."
    lines = [f"Schedules ({len(schedules)}):", ""]
    for rec in schedules:
        lines.append(_human_summary(rec))
        lines.append(f"  Schedule: {humanize_cron(str(rec.get('cron') or ''))}")
        lines.append(f"  Next: {_format_next_fire(rec.get('nextFireAt'))}")
        last = rec.get("lastStatus")
        last_str = f" | last: {str(last)[:80]}" if last else ""
        lines.append(f"  Fires so far: {rec.get('fireCount', 0)}{last_str}")
        lines.append(f"  Id: {rec.get('id')}")
        lines.append("")
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
