from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:  # pragma: no cover - exercised under AstrBot package imports
    from ..models.records import (
        FriendRequest,
        RiskDecision,
        SourceDetectionResult,
        normalize_id,
        normalize_id_set,
        now_ts,
    )
except ImportError:  # pragma: no cover - exercised by local pytest
    from models.records import (
        FriendRequest,
        RiskDecision,
        SourceDetectionResult,
        normalize_id,
        normalize_id_set,
        now_ts,
    )


@dataclass
class RiskConfig:
    enabled: bool = True
    auto_approve_enabled: bool = False
    dry_run: bool = True
    allowed_group_ids: set[str] = field(default_factory=set)
    blocked_group_ids: set[str] = field(default_factory=set)
    allowed_user_ids: set[str] = field(default_factory=set)
    blocked_user_ids: set[str] = field(default_factory=set)
    only_allow_whitelisted_groups: bool = True
    user_whitelist_bypass_group_rule: bool = False
    require_current_group_membership: bool = True
    require_nonempty_comment: bool = False
    blocked_comment_keywords: list[str] = field(default_factory=list)
    startup_grace_seconds: int = 120
    per_user_cooldown_seconds: int = 3600
    global_approvals_per_hour: int = 20
    per_group_approvals_per_hour: int = 5
    minimum_source_confidence: float = 0.7

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "RiskConfig":
        data = data or {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            auto_approve_enabled=bool(data.get("auto_approve_enabled", False)),
            dry_run=bool(data.get("dry_run", True)),
            allowed_group_ids=normalize_id_set(data.get("allowed_group_ids", [])),
            blocked_group_ids=normalize_id_set(data.get("blocked_group_ids", [])),
            allowed_user_ids=normalize_id_set(data.get("allowed_user_ids", [])),
            blocked_user_ids=normalize_id_set(data.get("blocked_user_ids", [])),
            only_allow_whitelisted_groups=bool(
                data.get("only_allow_whitelisted_groups", True)
            ),
            user_whitelist_bypass_group_rule=bool(
                data.get("user_whitelist_bypass_group_rule", False)
            ),
            require_current_group_membership=bool(
                data.get("require_current_group_membership", True)
            ),
            require_nonempty_comment=bool(data.get("require_nonempty_comment", False)),
            blocked_comment_keywords=[
                str(item).lower()
                for item in data.get("blocked_comment_keywords", [])
                if str(item).strip()
            ],
            startup_grace_seconds=max(0, int(data.get("startup_grace_seconds", 120))),
            per_user_cooldown_seconds=max(
                0, int(data.get("per_user_cooldown_seconds", 3600))
            ),
            global_approvals_per_hour=max(
                0, int(data.get("global_approvals_per_hour", 20))
            ),
            per_group_approvals_per_hour=max(
                0, int(data.get("per_group_approvals_per_hour", 5))
            ),
            minimum_source_confidence=float(
                data.get("minimum_source_confidence", 0.7)
            ),
        )


@dataclass
class RateLimitState:
    user_last_attempt: dict[str, float] = field(default_factory=dict)
    global_approvals: list[float] = field(default_factory=list)
    group_approvals: dict[str, list[float]] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "RateLimitState":
        data = data or {}
        return cls(
            user_last_attempt={
                normalize_id(k): float(v)
                for k, v in dict(data.get("user_last_attempt", {})).items()
            },
            global_approvals=[float(v) for v in data.get("global_approvals", [])],
            group_approvals={
                normalize_id(k): [float(v) for v in values]
                for k, values in dict(data.get("group_approvals", {})).items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_last_attempt": dict(self.user_last_attempt),
            "global_approvals": list(self.global_approvals),
            "group_approvals": {
                key: list(value) for key, value in self.group_approvals.items()
            },
        }

    def cleanup(self, now: float | None = None) -> None:
        now = now or now_ts()
        cutoff = now - 3600
        self.global_approvals = [ts for ts in self.global_approvals if ts >= cutoff]
        self.group_approvals = {
            group_id: [ts for ts in timestamps if ts >= cutoff]
            for group_id, timestamps in self.group_approvals.items()
        }
        self.group_approvals = {
            group_id: timestamps
            for group_id, timestamps in self.group_approvals.items()
            if timestamps
        }

    def record_attempt(self, user_id: str, now: float | None = None) -> None:
        self.user_last_attempt[normalize_id(user_id)] = now or now_ts()

    def record_approval(
        self, user_id: str, group_id: str | None, now: float | None = None
    ) -> None:
        now = now or now_ts()
        self.record_attempt(user_id, now)
        self.global_approvals.append(now)
        if group_id:
            group_id = normalize_id(group_id)
            self.group_approvals.setdefault(group_id, []).append(now)
        self.cleanup(now)


class RiskEvaluator:
    def __init__(
        self,
        config: RiskConfig,
        rate_limits: RateLimitState | None = None,
        started_at: float | None = None,
    ) -> None:
        self.config = config
        self.rate_limits = rate_limits or RateLimitState()
        self.started_at = started_at if started_at is not None else now_ts()

    def evaluate(
        self,
        request: FriendRequest,
        source: SourceDetectionResult,
        now: float | None = None,
    ) -> RiskDecision:
        now = now or now_ts()
        self.rate_limits.cleanup(now)
        user_id = normalize_id(request.user_id)
        group_id = normalize_id(source.group_id) if source.group_id else ""
        codes: list[str] = []

        if not self.config.enabled:
            return self._decision(False, "ignored", "disabled", ["plugin_disabled"])

        if user_id in self.config.blocked_user_ids:
            return self._decision(
                False, "ignored", "high", ["blocked_user"], "用户在黑名单中"
            )

        if group_id and group_id in self.config.blocked_group_ids:
            return self._decision(
                False, "ignored", "high", ["blocked_group"], "来源群在黑名单中"
            )

        comment_lower = (request.comment or "").lower()
        for keyword in self.config.blocked_comment_keywords:
            if keyword and keyword in comment_lower:
                return self._decision(
                    False,
                    "wait_manual",
                    "medium",
                    ["blocked_comment_keyword"],
                    "验证消息命中关键词黑名单",
                )

        if self.config.require_nonempty_comment and not request.comment.strip():
            return self._decision(
                False,
                "wait_manual",
                "unknown",
                ["empty_comment"],
                "配置要求验证消息非空",
            )

        whitelisted_user = user_id in self.config.allowed_user_ids
        if whitelisted_user:
            codes.append("allowed_user")

        if not whitelisted_user or not self.config.user_whitelist_bypass_group_rule:
            if not group_id:
                return self._decision(
                    False,
                    "wait_manual",
                    "unknown",
                    ["source_unknown"],
                    "无法可靠确定来源群",
                )
            if (
                self.config.only_allow_whitelisted_groups
                and group_id not in self.config.allowed_group_ids
            ):
                return self._decision(
                    False,
                    "wait_manual",
                    "unknown",
                    ["group_not_allowed"],
                    "来源群不在白名单中",
                )
            if source.confidence < self.config.minimum_source_confidence:
                return self._decision(
                    False,
                    "wait_manual",
                    "unknown",
                    ["source_confidence_low"],
                    "来源判断置信度不足",
                )
            if (
                self.config.require_current_group_membership
                and source.member_confirmed is not True
            ):
                return self._decision(
                    False,
                    "wait_manual",
                    "unknown",
                    ["membership_unconfirmed"],
                    "无法确认申请人仍在来源群内",
                )
            codes.append("trusted_group")

        grace_left = self.config.startup_grace_seconds - int(now - self.started_at)
        if grace_left > 0:
            return self._decision(
                False,
                "wait_manual",
                "unknown",
                ["startup_grace_period"],
                f"插件启动保护期剩余 {grace_left} 秒",
            )

        cooldown = self.config.per_user_cooldown_seconds
        last_attempt = self.rate_limits.user_last_attempt.get(user_id)
        if cooldown > 0 and last_attempt and now - last_attempt < cooldown:
            return self._decision(
                False,
                "wait_manual",
                "medium",
                ["user_cooldown"],
                "该用户申请仍在冷却时间内",
            )

        if (
            self.config.global_approvals_per_hour > 0
            and len(self.rate_limits.global_approvals)
            >= self.config.global_approvals_per_hour
        ):
            return self._decision(
                False,
                "wait_manual",
                "medium",
                ["global_hourly_limit"],
                "最近一小时全局自动同意数量已达上限",
            )

        if group_id and self.config.per_group_approvals_per_hour > 0:
            group_count = len(self.rate_limits.group_approvals.get(group_id, []))
            if group_count >= self.config.per_group_approvals_per_hour:
                return self._decision(
                    False,
                    "wait_manual",
                    "medium",
                    ["group_hourly_limit"],
                    "最近一小时该群自动同意数量已达上限",
                )

        if not self.config.auto_approve_enabled:
            return self._decision(
                False,
                "wait_manual",
                "low",
                [*codes, "auto_approve_disabled"],
                "规则判定为低风险，但自动同意开关未开启",
            )

        if self.config.dry_run:
            return self._decision(
                True,
                "dry_run_approve",
                "low",
                [*codes, "dry_run"],
                "规则判定为低风险；dry_run 模式不会真实同意",
            )

        return self._decision(
            True,
            "approve",
            "low",
            codes or ["low_risk"],
            "规则判定为低风险，允许自动同意",
        )

    @staticmethod
    def _decision(
        approved: bool,
        action: str,
        risk_level: str,
        reason_codes: list[str],
        message: str | None = None,
    ) -> RiskDecision:
        return RiskDecision(
            approved=approved,
            action=action,
            risk_level=risk_level,
            reason_codes=reason_codes,
            human_readable_reason=message or "；".join(reason_codes),
        )
