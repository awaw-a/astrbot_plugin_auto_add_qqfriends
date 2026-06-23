from __future__ import annotations

from models.records import FriendRequest, SourceDetectionResult, normalize_id_list
from services.risk_evaluator import RateLimitState, RiskConfig, RiskEvaluator


def _request(user_id: str = "12345", comment: str = "from group") -> FriendRequest:
    return FriendRequest(user_id=user_id, comment=comment, flag="flag-token")


def _source(group_id: str | None = "10001") -> SourceDetectionResult:
    return SourceDetectionResult(
        group_id=group_id,
        detection_method="explicit_event_field",
        confidence=0.95 if group_id else 0,
        candidate_groups=[group_id] if group_id else [],
        reason="test",
        member_confirmed=True if group_id else None,
    )


def _config(**overrides):
    base = {
        "enabled": True,
        "auto_approve_enabled": True,
        "dry_run": False,
        "allowed_group_ids": ["10001"],
        "only_allow_whitelisted_groups": True,
        "require_current_group_membership": True,
        "startup_grace_seconds": 0,
        "per_user_cooldown_seconds": 3600,
        "global_approvals_per_hour": 20,
        "per_group_approvals_per_hour": 5,
    }
    base.update(overrides)
    return RiskConfig.from_mapping(base)


def test_blocked_user_never_auto_approves():
    evaluator = RiskEvaluator(_config(blocked_user_ids=["12345"]), started_at=0)
    decision = evaluator.evaluate(_request(), _source(), now=100)
    assert not decision.approved
    assert decision.action == "ignored"
    assert "blocked_user" in decision.reason_codes


def test_blocked_group_never_auto_approves():
    evaluator = RiskEvaluator(_config(blocked_group_ids=["10001"]), started_at=0)
    decision = evaluator.evaluate(_request(), _source(), now=100)
    assert not decision.approved
    assert decision.action == "ignored"
    assert "blocked_group" in decision.reason_codes


def test_whitelisted_group_member_passes_basic_rules():
    evaluator = RiskEvaluator(_config(), started_at=0)
    decision = evaluator.evaluate(_request(), _source(), now=100)
    assert decision.approved
    assert decision.action == "approve"
    assert decision.risk_level == "low"


def test_unknown_source_waits_for_manual_review():
    evaluator = RiskEvaluator(_config(), started_at=0)
    decision = evaluator.evaluate(_request(), _source(None), now=100)
    assert not decision.approved
    assert decision.action == "wait_manual"
    assert "source_unknown" in decision.reason_codes


def test_dry_run_marks_eligible_without_real_approval_action():
    evaluator = RiskEvaluator(_config(dry_run=True), started_at=0)
    decision = evaluator.evaluate(_request(), _source(), now=100)
    assert decision.approved
    assert decision.action == "dry_run_approve"
    assert "dry_run" in decision.reason_codes


def test_user_cooldown_blocks_repeated_request():
    state = RateLimitState(user_last_attempt={"12345": 90})
    evaluator = RiskEvaluator(_config(), rate_limits=state, started_at=0)
    decision = evaluator.evaluate(_request(), _source(), now=100)
    assert not decision.approved
    assert "user_cooldown" in decision.reason_codes


def test_hourly_limits_block_auto_approval():
    state = RateLimitState(global_approvals=[10, 20])
    evaluator = RiskEvaluator(
        _config(global_approvals_per_hour=2),
        rate_limits=state,
        started_at=0,
    )
    decision = evaluator.evaluate(_request(), _source(), now=100)
    assert not decision.approved
    assert "global_hourly_limit" in decision.reason_codes


def test_numeric_and_string_ids_are_normalized():
    values = normalize_id_list([10001, "10001", 10002.0, " 10003 "])
    assert values == ["10001", "10002", "10003"]

