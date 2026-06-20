from __future__ import annotations

import re


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

_TRANSFORM_ONLY_PREFIXES = (
    "translate",
    "rewrite",
    "rephrase",
    "paraphrase",
    "summarize",
    "copyedit",
    "spellcheck",
    "fix grammar",
    "make this sound",
)

_TRANSFORM_ONLY_PATTERNS = (
    "translate this",
    "translate to ",
    "translate into ",
    "translate the phrase",
    "translate the sentence",
    "rewrite this",
    "rephrase this",
    "paraphrase this",
    "summarize this",
)

_SOURCE_ATTRIBUTED_ACTION_PATTERN = re.compile(
    r"\b(?:memory|memories|trace|log|logs|doc|document|report|ticket|screenshot|reply|message|status|board|canvas|previous\s+answer|old\s+context|prior\s+turn|route\s+history)\b"
    r"[^.?!]{0,80}"
    r"\b(?:says|say|said|claims|claimed|mentions|mentioned|contains|contained|shows|showed|tells|told|asks|asked|instructs|instructed)\b"
    r"[^.?!]{0,80}"
    r"\b(?:delete|cancel|remove|kill|stop|drop|disable|turn\s+off|build|create|make|run|launch|execute|dispatch|save|remember|publish|deploy|ship|change|set|switch|grant|revoke|propose|research|browse)\b",
    re.IGNORECASE,
)


def has_conversation_only_boundary(message: str) -> bool:
    lowered = str(message or "").strip().casefold()
    if not lowered:
        return False
    if _SOURCE_ATTRIBUTED_ACTION_PATTERN.search(lowered):
        return True
    if any(signal in lowered for signal in _META_LANGUAGE_SIGNALS):
        return True
    if lowered.startswith(_TRANSFORM_ONLY_PREFIXES) or any(
        signal in lowered for signal in _TRANSFORM_ONLY_PATTERNS
    ):
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
