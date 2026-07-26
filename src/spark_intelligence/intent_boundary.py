from __future__ import annotations


_META_LANGUAGE_SIGNALS = (
    "mentioning ",
    "just mentioning",
    "only mentioning",
    "keyword",
    "keywords",
    "word here",
    "words here",
    "word alone",
    "words alone",
    "phrase",
    "phrases",
    "term",
    "terms",
    "in this sentence",
    "as a sentence",
    "as words",
    "as text",
    "quoted text",
    "just quoted",
    "only quoted",
    "just a repo name",
    "just the repo name",
    "does not mean",
    "doesn't mean",
    "not mean",
    "talking about the word",
    "talking about the phrase",
    "discussing the word",
    "discussing the phrase",
    "not a request",
    "not an instruction",
    "not a command",
    "not asking for",
    "not asking you to",
)

_EXPLAIN_ONLY_SIGNALS = (
    "explain only",
    "just explain",
    "only explain",
    "explain the bug",
    "explain the issue",
    "talk here",
    "we can talk here",
    "stay in chat",
    "keep this conversational",
)


def has_conversation_only_boundary(message: str) -> bool:
    lowered = str(message or "").strip().casefold()
    if not lowered:
        return False
    if any(signal in lowered for signal in _META_LANGUAGE_SIGNALS):
        return True
    return any(signal in lowered for signal in _EXPLAIN_ONLY_SIGNALS) and any(
        denial in lowered for denial in ("do not ", "don't ", "without ", "no need to ", "not asking you to ")
    )


def denies_intent(message: str, actions: tuple[str, ...]) -> bool:
    lowered = str(message or "").strip().casefold()
    if not lowered:
        return False
    for action in actions:
        action = str(action or "").strip().casefold()
        if not action:
            continue
        denial_signals = (
            f"do not {action}",
            f"don't {action}",
            f"dont {action}",
            f"no need to {action}",
            f"not asking you to {action}",
            f"without {action}",
        )
        if any(signal in lowered for signal in denial_signals):
            return True
    return False
