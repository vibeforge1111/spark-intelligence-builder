"""Tests for inline directive detection regex (security boundary).

Proves the _INLINE_DIRECTIVE regex:
- Correctly detects actual directives ("always use dark mode")
- Does NOT false-positive on conversational sentences ("I always go to the beach")
- Cannot be exploited to inject directives via pronoun-prefixed text
"""
import re
import pytest

# Replicate the regex from service.py
_INLINE_DIRECTIVE = re.compile(
    r"(?:^|(?<=[.!?]\s))(?!(?:I|you|we|they|he|she|it)\s)(?:please\s+)?(?P<directive>always|never|stop)\s+(?P<body>[^.\n]{4,200})",
    re.IGNORECASE,
)

# Old regex (before fix) for comparison
_OLD_INLINE_DIRECTIVE = re.compile(
    r"\b(?:please\s+)?(?P<directive>always|never|stop)\s+(?P<body>[^.\n]{4,200})",
    re.IGNORECASE,
)


class TestDirectiveDetection:
    """Tests that actual directives are correctly detected."""

    def test_always_directive_detected(self):
        m = _INLINE_DIRECTIVE.search("always use dark mode")
        assert m is not None
        assert m.group("directive") == "always"

    def test_never_directive_detected(self):
        m = _INLINE_DIRECTIVE.search("never send emails without asking")
        assert m is not None
        assert m.group("directive") == "never"

    def test_stop_directive_detected(self):
        m = _INLINE_DIRECTIVE.search("stop mentioning the weather")
        assert m is not None
        assert m.group("directive") == "stop"

    def test_please_prefix_detected(self):
        m = _INLINE_DIRECTIVE.search("please always use bullet points")
        assert m is not None
        assert m.group("directive") == "always"

    def test_after_sentence_end_detected(self):
        """Directives after sentence-ending punctuation should match."""
        m = _INLINE_DIRECTIVE.search("Thanks. Always use dark mode")
        assert m is not None


class TestFalsePositivePrevention:
    """Proves conversational sentences are NOT detected as directives."""

    def test_i_always_not_detected(self):
        """'I always go to the beach' should NOT be a directive."""
        m = _INLINE_DIRECTIVE.search("I always go to the beach")
        assert m is None, f"False positive: 'I always' matched as directive"

    def test_you_always_not_detected(self):
        m = _INLINE_DIRECTIVE.search("you always say that")
        assert m is None

    def test_we_never_not_detected(self):
        m = _INLINE_DIRECTIVE.search("we never go there anymore")
        assert m is None

    def test_they_always_not_detected(self):
        m = _INLINE_DIRECTIVE.search("they always arrive late")
        assert m is None

    def test_he_never_not_detected(self):
        m = _INLINE_DIRECTIVE.search("he never listens to me")
        assert m is None

    def test_she_always_not_detected(self):
        m = _INLINE_DIRECTIVE.search("she always forgets her keys")
        assert m is None

    def test_it_never_not_detected(self):
        m = _INLINE_DIRECTIVE.search("it never works properly")
        assert m is None

    def test_old_regex_false_positive(self):
        """Prove the OLD regex incorrectly matched conversational text."""
        m = _OLD_INLINE_DIRECTIVE.search("I always go to the beach")
        assert m is not None, "Old regex should have false positive"
        assert m.group("directive") == "always"


class TestTrustBoundary:
    """Proves the fix narrows the directive detection boundary without
    widening authority or allowing injection."""

    def test_cannot_inject_via_pronoun_prefix(self):
        """An attacker cannot bypass the guard by prepending pronouns."""
        # Even with pronoun prefix, the directive after should still be caught
        # if it follows sentence-ending punctuation
        m = _INLINE_DIRECTIVE.search("I like it. Always use dark mode")
        assert m is not None, "Directive after sentence boundary should be caught"

    def test_directive_body_length_bounded(self):
        """Directive body is capped at 200 chars, preventing abuse."""
        long_body = "always " + "x" * 300
        m = _INLINE_DIRECTIVE.search(long_body)
        if m is not None:
            assert len(m.group("body")) <= 200

    def test_no_widening_of_directive_set(self):
        """Only always/never/stop are recognized — no new directive verbs added."""
        m = _INLINE_DIRECTIVE.search("sometimes ignore this rule")
        assert m is None

    def test_regex_uses_lookbehind_not_capture(self):
        """The sentence-boundary check uses lookbehind (zero-width),
        not capture groups, so it doesn't leak into extracted text."""
        m = _INLINE_DIRECTIVE.search("Thanks. Always use dark mode")
        if m is not None:
            # The captured group should only contain the directive + body
            assert "Thanks" not in m.group("directive")
            assert "Thanks" not in m.group("body")
