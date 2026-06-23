from __future__ import annotations

from models.records import FriendRequest, SourceDetectionResult, normalize_id_list
from services.risk_evaluator import RiskConfig, RiskEvaluator


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
        "blocked_group_ids": [],
        "accept_non_group_requests": False,
    }
    base.update(overrides)
    return RiskConfig.from_mapping(base)


def test_blocked_group_never_auto_approves():
    evaluator = RiskEvaluator(_config(blocked_group_ids=["10001"]))
    decision = evaluator.evaluate(_request(), _source())
    assert not decision.approved
    assert decision.action == "ignored"
    assert "blocked_group" in decision.reason_codes


def test_whitelisted_group_passes_rules():
    evaluator = RiskEvaluator(_config())
    decision = evaluator.evaluate(_request(), _source())
    assert decision.approved
    assert decision.action == "approve"
    assert decision.risk_level == "low"


def test_group_not_in_whitelist_waits_for_manual():
    evaluator = RiskEvaluator(_config(allowed_group_ids=["20002"]))
    decision = evaluator.evaluate(_request(), _source("10001"))
    assert not decision.approved
    assert decision.action == "wait_manual"
    assert "group_not_allowed" in decision.reason_codes


def test_empty_whitelist_allows_any_group():
    evaluator = RiskEvaluator(_config(allowed_group_ids=[]))
    decision = evaluator.evaluate(_request(), _source("99999"))
    assert decision.approved
    assert decision.action == "approve"


def test_non_group_request_blocked_by_default():
    evaluator = RiskEvaluator(_config())
    decision = evaluator.evaluate(_request(), _source(None))
    assert not decision.approved
    assert decision.action == "wait_manual"
    assert "non_group_request" in decision.reason_codes


def test_non_group_request_allowed_when_enabled():
    evaluator = RiskEvaluator(_config(accept_non_group_requests=True))
    decision = evaluator.evaluate(_request(), _source(None))
    assert decision.approved
    assert decision.action == "approve"


def test_dry_run_marks_eligible_without_real_approval():
    evaluator = RiskEvaluator(_config(dry_run=True))
    decision = evaluator.evaluate(_request(), _source())
    assert decision.approved
    assert decision.action == "dry_run_approve"
    assert "dry_run" in decision.reason_codes


def test_auto_approve_disabled_waits_for_manual():
    evaluator = RiskEvaluator(_config(auto_approve_enabled=False))
    decision = evaluator.evaluate(_request(), _source())
    assert not decision.approved
    assert decision.action == "wait_manual"
    assert "auto_approve_disabled" in decision.reason_codes


def test_plugin_disabled_ignored():
    evaluator = RiskEvaluator(_config(enabled=False))
    decision = evaluator.evaluate(_request(), _source())
    assert not decision.approved
    assert decision.action == "ignored"
    assert "plugin_disabled" in decision.reason_codes


def test_numeric_and_string_ids_are_normalized():
    values = normalize_id_list([10001, "10001", 10002.0, " 10003 "])
    assert values == ["10001", "10002", "10003"]
