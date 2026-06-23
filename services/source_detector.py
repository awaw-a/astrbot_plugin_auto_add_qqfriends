from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Protocol

try:  # pragma: no cover
    from ..models.records import (
        FriendRequest,
        SourceDetectionResult,
        normalize_id,
        normalize_id_list,
    )
except ImportError:  # pragma: no cover
    from models.records import (
        FriendRequest,
        SourceDetectionResult,
        normalize_id,
        normalize_id_list,
    )


class GroupContextLookup(Protocol):
    async def groups_for_user(
        self,
        user_id: str,
        allowed_group_ids: Iterable[str] | None = None,
        within_seconds: int | None = None,
    ) -> list[str]:
        ...


class OneBotLookup(Protocol):
    async def is_group_member(self, group_id: str, user_id: str) -> bool | None:
        ...


_EXPLICIT_GROUP_FIELDS = (
    "group_id",
    "source_group_id",
    "from_group_id",
    "request_group_id",
    "temp_source_group_id",
)
_NESTED_GROUP_FIELDS = ("source", "group", "sender", "request", "context")
_STRUCTURED_GROUP_RE = re.compile(
    r"(?:group_id|source_group_id|from_group_id|群号|来源群)\s*[:：= ]\s*(\d{5,12})",
    re.IGNORECASE,
)


class SourceDetector:
    def __init__(
        self,
        allowed_group_ids: Iterable[str] | None = None,
        context_time_window_seconds: int = 86400,
    ) -> None:
        self.allowed_group_ids = set(normalize_id_list(allowed_group_ids or []))
        self.context_time_window_seconds = max(0, int(context_time_window_seconds))

    async def detect(
        self,
        request: FriendRequest,
        bridge: OneBotLookup | None = None,
        context_cache: GroupContextLookup | None = None,
        require_membership: bool = True,
    ) -> SourceDetectionResult:
        raw = request.raw_event or {}

        explicit = self._extract_explicit_group(raw)
        if explicit:
            return await self._result_for_explicit_group(
                explicit,
                request.user_id,
                "explicit_event_field",
                bridge,
                require_membership,
            )

        extension = self._extract_extension_group(raw)
        if extension:
            return await self._result_for_explicit_group(
                extension,
                request.user_id,
                "onebot_extension_field",
                bridge,
                require_membership,
            )

        structured = self._parse_structured_comment(request.comment)
        if structured:
            return await self._result_for_explicit_group(
                structured,
                request.user_id,
                "structured_comment",
                bridge,
                require_membership,
                base_confidence=0.78,
            )

        if bridge and self.allowed_group_ids:
            api_candidates: list[str] = []
            for group_id in sorted(self.allowed_group_ids):
                member = await bridge.is_group_member(group_id, request.user_id)
                if member is True:
                    api_candidates.append(group_id)
            api_result = self._result_from_candidates(
                api_candidates,
                "allowed_group_membership_api",
                "通过 get_group_member_info 在允许群中确认成员关系",
                confidence=0.86,
                member_confirmed=True,
            )
            if api_result:
                return api_result

        if context_cache:
            candidates = await context_cache.groups_for_user(
                request.user_id,
                self.allowed_group_ids or None,
                self.context_time_window_seconds or None,
            )
            context_result = self._result_from_candidates(
                candidates,
                "recent_group_context",
                "根据近期群聊活动缓存辅助匹配来源群",
                confidence=0.74,
                member_confirmed=None,
            )
            if context_result:
                return context_result

        return SourceDetectionResult(
            detection_method="unknown",
            confidence=0.0,
            candidate_groups=[],
            reason="未找到明确来源群，且无法从共同群或近期群聊缓存唯一确认",
            member_confirmed=None,
        )

    async def _result_for_explicit_group(
        self,
        group_id: str,
        user_id: str,
        method: str,
        bridge: OneBotLookup | None,
        require_membership: bool,
        base_confidence: float = 0.95,
    ) -> SourceDetectionResult:
        member_confirmed: bool | None = None
        confidence = base_confidence
        reason = "原始 request 事件中包含明确来源群字段"
        if bridge and require_membership:
            member_confirmed = await bridge.is_group_member(group_id, user_id)
            if member_confirmed is True:
                confidence = min(1.0, confidence + 0.03)
                reason += "，且已确认申请人仍在群内"
            elif member_confirmed is False:
                confidence = min(confidence, 0.45)
                reason += "，但未能确认申请人仍在群内"
            else:
                confidence = min(confidence, 0.65)
                reason += "，成员关系接口不可用或返回不确定"
        return SourceDetectionResult(
            group_id=normalize_id(group_id),
            detection_method=method,
            confidence=confidence,
            candidate_groups=[normalize_id(group_id)],
            reason=reason,
            member_confirmed=member_confirmed,
        )

    def _result_from_candidates(
        self,
        candidates: Iterable[str],
        method: str,
        reason: str,
        confidence: float,
        member_confirmed: bool | None,
    ) -> SourceDetectionResult | None:
        normalized = normalize_id_list(candidates)
        if not normalized:
            return None
        if len(normalized) == 1:
            return SourceDetectionResult(
                group_id=normalized[0],
                detection_method=method,
                confidence=confidence,
                candidate_groups=normalized,
                reason=reason,
                member_confirmed=member_confirmed,
            )
        return SourceDetectionResult(
            group_id=None,
            detection_method="ambiguous",
            confidence=0.2,
            candidate_groups=normalized,
            reason=f"{reason}，但候选群不唯一",
            member_confirmed=member_confirmed,
        )

    @staticmethod
    def _extract_explicit_group(raw: dict[str, Any]) -> str:
        for key in _EXPLICIT_GROUP_FIELDS:
            group_id = normalize_id(raw.get(key))
            if group_id:
                return group_id
        return ""

    @staticmethod
    def _extract_extension_group(raw: dict[str, Any]) -> str:
        for key in _NESTED_GROUP_FIELDS:
            nested = raw.get(key)
            if not isinstance(nested, dict):
                continue
            for field in _EXPLICIT_GROUP_FIELDS:
                group_id = normalize_id(nested.get(field))
                if group_id:
                    return group_id
        for key, value in raw.items():
            if "group" not in str(key).lower():
                continue
            group_id = normalize_id(value)
            if group_id and group_id.isdigit():
                return group_id
        return ""

    @staticmethod
    def _parse_structured_comment(comment: str) -> str:
        if not comment:
            return ""
        match = _STRUCTURED_GROUP_RE.search(comment)
        if not match:
            return ""
        return normalize_id(match.group(1))
