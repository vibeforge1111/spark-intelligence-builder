from spark_intelligence.creator import build_creator_intent_packet


def test_creator_plan_detects_full_startup_yc_swarm_flow():
    packet = build_creator_intent_packet(
        "Create a Startup YC specialization path with benchmarked autoloop from Telegram and Spark Swarm"
    )

    assert packet.schema_version == "spark-creator-intent.v1"
    assert packet.target_domain == "startup-yc"
    assert packet.privacy_mode == "swarm_shared"
    assert packet.risk_level == "medium"
    assert packet.desired_outputs["domain_chip"] is True
    assert packet.desired_outputs["specialization_path"] is True
    assert packet.desired_outputs["benchmark_pack"] is True
    assert packet.desired_outputs["autoloop_policy"] is True
    assert packet.desired_outputs["telegram_flow"] is True
    assert packet.desired_outputs["swarm_publish_packet"] is True
    assert "spark_telegram_bot" in packet.tools_in_scope
    assert "spark_swarm" in packet.tools_in_scope
    assert packet.target_operator_surface == "telegram+builder+swarm"


def test_creator_plan_defaults_domain_chip_to_benchmarked_local_work():
    packet = build_creator_intent_packet("Make Spark good at investor diligence")

    assert packet.target_domain == "spark-good-investor-diligence"
    assert packet.privacy_mode == "local_only"
    assert packet.desired_outputs["domain_chip"] is True
    assert packet.desired_outputs["benchmark_pack"] is True
    assert packet.desired_outputs["specialization_path"] is False
    assert packet.desired_outputs["autoloop_policy"] is False


def test_creator_plan_honors_explicit_private_mode():
    packet = build_creator_intent_packet(
        "Build a github repo backed benchmark for founder research but keep it private"
    )

    assert packet.privacy_mode == "local_only"
    assert packet.risk_level == "low"
    assert "github" in packet.tools_in_scope


def test_creator_plan_marks_recursive_publish_as_medium_risk():
    packet = build_creator_intent_packet(
        "Create a recursive benchmark and autoloop for Spark Telegram bot and publish learnings to the network"
    )

    assert packet.target_domain == "spark-telegram-bot"
    assert packet.privacy_mode == "swarm_shared"
    assert packet.risk_level == "medium"
    assert packet.desired_outputs["autoloop_policy"] is True
    assert packet.desired_outputs["swarm_publish_packet"] is True

