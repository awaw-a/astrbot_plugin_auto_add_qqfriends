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
    )
except ImportError:  # pragma: no cover - exercised by local pytest
    from models.records import (
        FriendRequest,
        RiskDecision,
        SourceDetectionResult,
        normalize_id,
        normalize_id_set,
    )


@dataclass
class RiskConfig:
    enabled: bool = True
    auto_approve_enabled: bool = False
    dry_run: bool = True
    allowed_group_ids: set[str] = field(default_factory=set)
    blocked_group_ids: set[str] = field(default_factory=set)
    accept_non_group_requests: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "RiskConfig":
        data = data or {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            auto_approve_enabled=bool(data.get("auto_approve_enabled", False)),
            dry_run=bool(data.get("dry_run", True)),
            allowed_group_ids=normalize_id_set(data.get("allowed_group_ids", [])),
            blocked_group_ids=normalize_id_set(data.get("blocked_group_ids", [])),
            accept_non_group_requests=bool(
                data.get("accept_non_group_requests", False)
            ),
        )


class RiskEvaluator:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def evaluate(
        self,
        request: FriendRequest,
        source: SourceDetectionResult,
    ) -> RiskDecision:
        if not self.config.enabled:
            return self._decision(False, "ignored", "disabled", ["plugin_disabled"])

        group_id = normalize_id(source.group_id) if source.group_id else ""

        if group_id and group_id in self.config.blocked_group_ids:
            return self._decision(
                False, "ignored", "high", ["blocked_group"], "来源群在黑名单中"
            )

        if group_id:
            if (
                self.config.allowed_group_ids
                and group_id not in self.config.allowed_group_ids
            ):
                return self._decision(
                    False,
                    "wait_manual",
                    "unknown",
                    ["group_not_allowed"],
                    "来源群不在白名单中",
                )
        else:
            if not self.config.accept_non_group_requests:
                return self._decision(
                    False,
                    "wait_manual",
                    "unknown",
                    ["non_group_request"],
                    "非群聊来源好友申请，且未开启接受非群聊来源",
                )

        if not self.config.auto_approve_enabled:
            return self._decision(
                False,
                "wait_manual",
                "low",
                ["auto_approve_disabled"],
                "规则判定通过，但自动同意开关未开启",
            )

        if self.config.dry_run:
            return self._decision(
                True,
                "dry_run_approve",
                "low",
                ["dry_run"],
                "规则判定通过；dry_run 模式不会真实同意",
            )

        return self._decision(
            True,
            "approve",
            "low",
            ["approved"],
            "规则判定通过，允许自动同意",
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
