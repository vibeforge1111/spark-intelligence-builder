from spark_intelligence.gateway.guardrails import _strip_em_dashes


def test_strip_em_dashes_preserves_unrelated_indentation() -> None:
    text = "Plan:\n  1. ship it\n  2. measure \u2014 then iterate"
    assert _strip_em_dashes(text) == "Plan:\n  1. ship it\n  2. measure - then iterate"


def test_strip_em_dashes_preserves_unrelated_double_spaces() -> None:
    assert _strip_em_dashes("a  b") == "a  b"


def test_strip_em_dashes_single_spaces_adjacent_dash_whitespace() -> None:
    assert _strip_em_dashes("word \u2014 word") == "word - word"
    assert _strip_em_dashes("word\u2014word") == "word - word"
