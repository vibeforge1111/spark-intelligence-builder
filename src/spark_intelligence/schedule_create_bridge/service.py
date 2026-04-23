from __future__ import annotations

import re
from typing import Any

_DOW = {
    "sunday": 0, "sun": 0,
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3,
    "thursday": 4, "thu": 4, "thurs": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
}

_TIME_OF_DAY = {
    "morning": 9,
    "afternoon": 14,
    "evening": 18,
    "night": 21,
    "midnight": 0,
    "noon": 12,
}


def _parse_hour(text: str) -> int | None:
    """Parse '9', '9 am', '9pm', '15', '3:30am' -> hour in 24h form."""
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text.strip(), re.IGNORECASE)
    if not m:
        return None
    h = int(m.group(1))
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    if h < 0 or h > 23:
        return None
    return h


def humanize_to_cron(text: str) -> str | None:
    """Convert a natural-language schedule expression to a cron string.

    Returns None if no confident parse. Callers should treat None as
    'ask user for explicit cron'.
    """
    t = text.strip().lower()
    if not t:
        return None

    # hourly, daily, weekly, monthly, yearly
    if re.search(r"\bhourly\b", t):
        return "0 * * * *"
    if re.search(r"\bdaily\b", t) and not re.search(r"\bat\b", t):
        return "0 9 * * *"
    if re.search(r"\bweekly\b", t) and not re.search(r"\b(on|at)\b", t):
        return "0 9 * * 1"
    if re.search(r"\bmonthly\b", t) and not re.search(r"\b(on|at)\b", t):
        return "0 9 1 * *"
    if re.search(r"\byearly\b", t) and not re.search(r"\b(on|at)\b", t):
        return "0 9 1 1 *"

    # "every N minutes|hours|days"
    m = re.search(r"every\s+(\d+)\s+(minute|hour|day)s?", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "minute" and 1 <= n <= 59:
            return f"*/{n} * * * *"
        if unit == "hour" and 1 <= n <= 23:
            return f"0 */{n} * * *"
        if unit == "day" and 1 <= n <= 30:
            return f"0 9 */{n} * *"

    # "every <dow> [at H am/pm]"
    m = re.search(r"every\s+(sunday|monday|tuesday|wednesday|thursday|friday|saturday|sun|mon|tue|tues|wed|thu|thurs|fri|sat)(?:\s+at\s+([\d:apm\s]+))?", t)
    if m:
        dow = _DOW[m.group(1).lower()]
        hour_str = m.group(2) or "9 am"
        hour = _parse_hour(hour_str) or 9
        return f"0 {hour} * * {dow}"

    # "every weekday [at H]" -> Mon-Fri
    m = re.search(r"every\s+weekday(?:\s+at\s+([\d:apm\s]+))?", t)
    if m:
        hour = _parse_hour(m.group(1) or "9 am") or 9
        return f"0 {hour} * * 1-5"

    # "every morning|afternoon|evening|night [at H]"
    m = re.search(r"every\s+(morning|afternoon|evening|night|midnight|noon)(?:\s+at\s+([\d:apm\s]+))?", t)
    if m:
        default_hour = _TIME_OF_DAY[m.group(1)]
        hour = _parse_hour(m.group(2)) if m.group(2) else default_hour
        if hour is None:
            hour = default_hour
        return f"0 {hour} * * *"

    # "(every day|daily) at H am/pm"
    m = re.search(r"(?:every\s+day|daily)\s+at\s+([\d:apm\s]+)", t)
    if m:
        hour = _parse_hour(m.group(1))
        if hour is not None:
            return f"0 {hour} * * *"

    # "at H am/pm every day" / bare "at H am|pm"
    m = re.search(r"at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", t)
    if m:
        hour = _parse_hour(m.group(1))
        if hour is not None:
            return f"0 {hour} * * *"

    return None


# Scheduling intent detector. Must include a schedule verb + a time expression
# AND ideally an action (chip key or mission goal). We detect on the verb +
# time; the action extraction can be downstream.
_SCHEDULE_VERBS = r"schedule|automate|set\s+up|recur(?:ring)?|fire|run|trigger"
_TIME_EXPR = (
    r"every\s+\w+|daily|hourly|weekly|monthly|yearly|nightly|"
    r"each\s+(?:day|morning|evening|night|week)|at\s+\d+\s*(?:am|pm)|"
    r"every\s+(?:morning|afternoon|evening|night|weekday)"
)

_SCHEDULE_CREATE_PATTERNS = (
    # "schedule X every day", "automate X hourly", "set up daily X"
    re.compile(rf"\b(?:{_SCHEDULE_VERBS})\b[^.\n]{{0,80}}\b(?:{_TIME_EXPR})\b", re.IGNORECASE),
    # Leading time "every morning research X", "daily at 9 run Y"
    re.compile(rf"\b(?:{_TIME_EXPR})\b[^.\n]{{0,80}}\b(?:{_SCHEDULE_VERBS})\b", re.IGNORECASE),
)

# Negative anchors - queries about schedules, not creating them
_NEGATIVE_ANCHORS = (
    re.compile(r"\b(?:show|list|display|what|whats?|view|see|get)\b[^.\n]{0,30}\b(?:schedule|scheduled|cron|running|automation)\b", re.IGNORECASE),
    re.compile(r"\b(?:cancel|delete|kill|remove|stop|drop|disable)\b[^.\n]{0,30}\b(?:schedule|scheduled|cron)\b", re.IGNORECASE),
)


def _extract_action(text: str, cron: str | None) -> dict[str, Any]:
    """Guess whether the scheduled action is a mission or a loop."""
    # Chip key reference -> loop
    chip_match = re.search(r"\b(domain-chip-[\w\-]+|startup-yc|spark-browser|spark-swarm)\b", text, re.IGNORECASE)
    if chip_match:
        return {"type": "loop", "chip_key": chip_match.group(1).lower(), "rounds": 1}
    # Otherwise assume mission, strip the time expression + schedule verbs from the text
    cleaned = text
    for rx in (
        r"\b(?:please\s+)?(?:every|daily|hourly|weekly|monthly|yearly|nightly|each)\b[^.\n]{0,50}",
        rf"\b(?:{_SCHEDULE_VERBS})\b",
        r"\bat\s+\d+\s*(?:am|pm)?\b",
    ):
        cleaned = re.sub(rx, " ", cleaned, flags=re.IGNORECASE)
    goal = re.sub(r"\s+", " ", cleaned).strip(" .,!?")
    return {"type": "mission", "goal": goal or "(no goal provided)"}


def detect_schedule_create_intent(message: str) -> dict | None:
    text = str(message or "").strip()
    if not text:
        return None
    for neg in _NEGATIVE_ANCHORS:
        if neg.search(text):
            return None
    matched = any(p.search(text) for p in _SCHEDULE_CREATE_PATTERNS)
    if not matched:
        return None
    cron = humanize_to_cron(text)
    action = _extract_action(text, cron)
    return {
        "action": "schedule_create",
        "cron": cron,
        "target": action,
        "raw": text,
    }


def format_schedule_create_suggestion(intent: dict[str, Any]) -> str:
    cron = intent.get("cron")
    target = intent.get("target") or {}
    ttype = target.get("type", "mission")
    if not cron:
        return (
            "I can tell you want to schedule something but I can't pin down "
            "the exact cadence. Try: 'every day at 9am ...', 'every 30 minutes ...', "
            "'every Monday at 3pm ...', or give me the cron directly: "
            '/schedule "<cron>" mission|loop <payload>'
        )
    if ttype == "loop":
        chip = target.get("chip_key", "?")
        rounds = target.get("rounds", 1)
        return (
            f"Got it - recurring loop on {chip}, {rounds} round{'s' if rounds != 1 else ''} per fire. "
            f'Tap /schedule "{cron}" loop {chip} {rounds} to set it up.'
        )
    goal = target.get("goal", "(no goal)")
    return (
        f"Got it - recurring mission on that cadence. "
        f'Tap /schedule "{cron}" mission {goal} to set it up.'
    )
