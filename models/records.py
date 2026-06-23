from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = 1

_SENSITIVE_PATTERNS = [
    re.compile(
        r"(?i)\b(access[_-]?token|authorization|api[_-]?key|secret|password|passwd|cookie)\b"
        r"\s*[:=]\s*([^\s;,&]+)"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
]


def now_ts() -> float:
    return time.time()


def normalize_id(value: Any) -> str:
    """Normalize QQ/group IDs to strings without treating them as numbers."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value).strip()
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def normalize_id_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, int, float)):
        values = [values]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_id(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def normalize_id_set(values: Any) -> set[str]:
    return set(normalize_id_list(values))


def flag_digest(flag: str | None) -> str:
    if not flag:
        return ""
    return "sha256:" + hashlib.sha256(flag.encode("utf-8")).hexdigest()[:18]


def mask_id(value: Any) -> str:
    text = normalize_id(value)
    if not text:
        return ""
    if len(text) <= 4:
        return text[0] + "***"
    return f"{text[:3]}***{text[-2:]}"


def truncate_text(text: str | None, limit: int = 500) -> str:
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def redact_sensitive_text(text: str | None) -> str:
    if not text:
        return ""
    redacted = str(text)
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.pattern.startswith("(?i)\\bBearer"):
            redacted = pattern.sub("Bearer [REDACTED]", redacted)
        else:
            redacted = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", redacted)
    return redacted


@dataclass
class FriendRequest:
    user_id: str
    comment: str = ""
    flag: str = ""
    sub_type: str = ""
    time: float = 0
    self_id: str = ""
    raw_event: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, raw_event: dict[str, Any]) -> "FriendRequest":
        return cls(
            user_id=normalize_id(raw_event.get("user_id")),
            comment=str(raw_event.get("comment") or ""),
            flag=str(raw_event.get("flag") or ""),
            sub_type=str(raw_event.get("sub_type") or ""),
            time=float(raw_event.get("time") or now_ts()),
            self_id=normalize_id(raw_event.get("self_id")),
            raw_event=dict(raw_event),
        )

    @property
    def safe_flag_digest(self) -> str:
        return flag_digest(self.flag)


@dataclass
class SourceDetectionResult:
    group_id: str | None = None
    detection_method: str = "unknown"
    confidence: float = 0.0
    candidate_groups: list[str] = field(default_factory=list)
    reason: str = ""
    member_confirmed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RiskDecision:
    approved: bool
    action: str
    risk_level: str
    reason_codes: list[str]
    human_readable_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GroupMessageRecord:
    group_id: str
    group_name: str
    sender_id: str
    sender_name: str
    text: str
    timestamp: float
    is_bot: bool = False
    message_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupMessageRecord":
        return cls(
            group_id=normalize_id(data.get("group_id")),
            group_name=str(data.get("group_name") or ""),
            sender_id=normalize_id(data.get("sender_id")),
            sender_name=str(data.get("sender_name") or ""),
            text=str(data.get("text") or ""),
            timestamp=float(data.get("timestamp") or 0),
            is_bot=bool(data.get("is_bot", False)),
            message_id=str(data.get("message_id") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UserGroupAssociation:
    user_id: str
    group_id: str
    detection_method: str
    confidence: float
    approved_at: float
    expires_at: float
    first_injected_at: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserGroupAssociation":
        return cls(
            user_id=normalize_id(data.get("user_id")),
            group_id=normalize_id(data.get("group_id")),
            detection_method=str(data.get("detection_method") or "unknown"),
            confidence=float(data.get("confidence") or 0),
            approved_at=float(data.get("approved_at") or 0),
            expires_at=float(data.get("expires_at") or 0),
            first_injected_at=(
                float(data["first_injected_at"])
                if data.get("first_injected_at") is not None
                else None
            ),
        )

    def is_expired(self, now: float | None = None) -> bool:
        return (now or now_ts()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessedRequestRecord:
    user_id: str
    flag_digest: str
    comment: str
    source_group_id: str | None
    source_method: str
    source_confidence: float
    risk_level: str
    action: str
    result: str
    timestamp: float
    failure_reason: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcessedRequestRecord":
        return cls(
            user_id=normalize_id(data.get("user_id")),
            flag_digest=str(data.get("flag_digest") or ""),
            comment=str(data.get("comment") or ""),
            source_group_id=(
                normalize_id(data.get("source_group_id"))
                if data.get("source_group_id")
                else None
            ),
            source_method=str(data.get("source_method") or "unknown"),
            source_confidence=float(data.get("source_confidence") or 0),
            risk_level=str(data.get("risk_level") or "unknown"),
            action=str(data.get("action") or ""),
            result=str(data.get("result") or ""),
            timestamp=float(data.get("timestamp") or 0),
            failure_reason=str(data.get("failure_reason") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PendingRequestRecord:
    user_id: str
    flag_digest: str
    comment: str
    source_group_id: str | None
    source_method: str
    risk_level: str
    reason: str
    created_at: float
    expires_at: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingRequestRecord":
        return cls(
            user_id=normalize_id(data.get("user_id")),
            flag_digest=str(data.get("flag_digest") or ""),
            comment=str(data.get("comment") or ""),
            source_group_id=(
                normalize_id(data.get("source_group_id"))
                if data.get("source_group_id")
                else None
            ),
            source_method=str(data.get("source_method") or "unknown"),
            risk_level=str(data.get("risk_level") or "unknown"),
            reason=str(data.get("reason") or ""),
            created_at=float(data.get("created_at") or 0),
            expires_at=float(data.get("expires_at") or 0),
        )

    def is_expired(self, now: float | None = None) -> bool:
        return (now or now_ts()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
