import pytest
from spark_intelligence.creator.intent import _infer_desired_outputs


def test_telegram_brief_does_not_force_domain_chip():
    result = _infer_desired_outputs("build me a telegram bot")
    assert result["domain_chip"] is False


def test_bot_brief_does_not_force_domain_chip():
    result = _infer_desired_outputs("set up a chat bot")
    assert result["domain_chip"] is False


def test_spawner_brief_does_not_force_domain_chip():
    result = _infer_desired_outputs("create a spawner mission canvas")
    assert result["domain_chip"] is False


def test_kanban_brief_does_not_force_domain_chip():
    result = _infer_desired_outputs("set up a kanban board with missions")
    assert result["domain_chip"] is False


def test_mission_brief_does_not_force_domain_chip():
    result = _infer_desired_outputs("build trackable missions")
    assert result["domain_chip"] is False


def test_explicit_chip_brief_enables_domain_chip():
    result = _infer_desired_outputs("create a domain chip for research")
    assert result["domain_chip"] is True


def test_plain_brief_defaults_domain_chip_true():
    result = _infer_desired_outputs("help me build something")
    assert result["domain_chip"] is True


def test_telegram_and_chip_keywords_together():
    result = _infer_desired_outputs("build a telegram bot with a domain chip")
    assert result["domain_chip"] is True