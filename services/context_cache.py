from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover
    from ..models.records import (
        GroupMessageRecord,
        normalize_id,
        normalize_id_list,
        now_ts,
        redact_sensitive_text,
        truncate_text,
    )
except ImportError:  # pragma: no cover
    from models.records import (
        GroupMessageRecord,
        normalize_id,
        normalize_id_list,
        now_ts,
        redact_sensitive_text,
        truncate_text,
    )


@dataclass
class ContextCacheConfig:
    enabled: bool = True
    context_ttl_seconds: int = 86400
    max_cached_groups: int = 100
    max_messages_per_group: int = 80
    max_message_length: int = 500
    redact_sensitive_text: bool = True

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "ContextCacheConfig":
        data = data or {}
        return cls(
            enabled=bool(data.get("context_cache_enabled", True)),
            context_ttl_seconds=max(60, int(data.get("context_ttl_seconds", 86400))),
            max_cached_groups=max(1, int(data.get("max_cached_groups", 100))),
            max_messages_per_group=max(
                1, int(data.get("max_messages_per_group", 80))
            ),
            max_message_length=max(20, int(data.get("max_message_length", 500))),
            redact_sensitive_text=bool(data.get("redact_sensitive_text", True)),
        )


class ContextCache:
    def __init__(self, config: ContextCacheConfig | None = None) -> None:
        self.config = config or ContextCacheConfig()
        self._records: dict[str, deque[GroupMessageRecord]] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def add_message(self, record: GroupMessageRecord) -> bool:
        if not self.config.enabled:
            return False
        record.group_id = normalize_id(record.group_id)
        record.sender_id = normalize_id(record.sender_id)
        if not record.group_id or not record.sender_id:
            return False
        text = (record.text or "").strip()
        if not text or text.startswith("/"):
            return False
        if self.config.redact_sensitive_text:
            text = redact_sensitive_text(text)
        record.text = truncate_text(text, self.config.max_message_length)
        async with self._get_lock():
            bucket = self._records.setdefault(record.group_id, deque())
            bucket.append(record)
            while len(bucket) > self.config.max_messages_per_group:
                bucket.popleft()
            await self._enforce_group_limit_locked()
        return True

    async def clear_group(self, group_id: str) -> int:
        async with self._get_lock():
            bucket = self._records.pop(normalize_id(group_id), deque())
            return len(bucket)

    async def cleanup_expired(self, now: float | None = None) -> int:
        now = now or now_ts()
        cutoff = now - self.config.context_ttl_seconds
        removed = 0
        async with self._get_lock():
            empty: list[str] = []
            for group_id, bucket in self._records.items():
                original = len(bucket)
                while bucket and bucket[0].timestamp < cutoff:
                    bucket.popleft()
                removed += original - len(bucket)
                if not bucket:
                    empty.append(group_id)
            for group_id in empty:
                self._records.pop(group_id, None)
        return removed

    async def groups_for_user(
        self,
        user_id: str,
        allowed_group_ids: Iterable[str] | None = None,
        within_seconds: int | None = None,
    ) -> list[str]:
        user_id = normalize_id(user_id)
        allowed = set(normalize_id_list(allowed_group_ids or []))
        cutoff = now_ts() - within_seconds if within_seconds else None
        matches: list[str] = []
        async with self._get_lock():
            for group_id, bucket in self._records.items():
                if allowed and group_id not in allowed:
                    continue
                if any(
                    item.sender_id == user_id
                    and (cutoff is None or item.timestamp >= cutoff)
                    for item in bucket
                ):
                    matches.append(group_id)
        return matches

    async def select_context(
        self,
        user_id: str,
        group_id: str,
        max_messages: int = 8,
        before: int = 2,
        after: int = 1,
        max_chars: int = 2000,
        time_window_seconds: int = 86400,
    ) -> list[GroupMessageRecord]:
        user_id = normalize_id(user_id)
        group_id = normalize_id(group_id)
        max_messages = max(1, max_messages)
        cutoff = now_ts() - max(0, time_window_seconds)
        async with self._get_lock():
            records = [
                item
                for item in list(self._records.get(group_id, deque()))
                if item.timestamp >= cutoff
            ]
        if not records:
            return []

        selected_indexes: list[int] = []
        own_indexes = [idx for idx, item in enumerate(records) if item.sender_id == user_id]
        if own_indexes:
            for idx in own_indexes[-max_messages:]:
                start = max(0, idx - before)
                end = min(len(records), idx + after + 1)
                for candidate in range(start, end):
                    if candidate not in selected_indexes:
                        selected_indexes.append(candidate)
                    if len(selected_indexes) >= max_messages:
                        break
                if len(selected_indexes) >= max_messages:
                    break
        else:
            selected_indexes = list(range(max(0, len(records) - max_messages), len(records)))

        selected = [records[idx] for idx in sorted(selected_indexes)]
        trimmed: list[GroupMessageRecord] = []
        used_chars = 0
        for item in selected:
            text_len = len(item.text)
            if trimmed and used_chars + text_len > max_chars:
                break
            if text_len > max_chars:
                item = GroupMessageRecord(
                    group_id=item.group_id,
                    group_name=item.group_name,
                    sender_id=item.sender_id,
                    sender_name=item.sender_name,
                    text=truncate_text(item.text, max_chars),
                    timestamp=item.timestamp,
                    is_bot=item.is_bot,
                    message_id=item.message_id,
                )
                text_len = len(item.text)
            trimmed.append(item)
            used_chars += text_len
        return trimmed

    async def stats(self) -> dict[str, int]:
        async with self._get_lock():
            return {
                "groups": len(self._records),
                "messages": sum(len(bucket) for bucket in self._records.values()),
            }

    async def to_json(self) -> dict[str, Any]:
        async with self._get_lock():
            return {
                "groups": {
                    group_id: [record.to_dict() for record in bucket]
                    for group_id, bucket in self._records.items()
                }
            }

    async def load_json(self, data: dict[str, Any] | None) -> None:
        data = data or {}
        groups = data.get("groups", {})
        async with self._get_lock():
            self._records.clear()
            for group_id, rows in dict(groups).items():
                bucket = deque(
                    GroupMessageRecord.from_dict(row)
                    for row in rows
                    if isinstance(row, dict)
                )
                while len(bucket) > self.config.max_messages_per_group:
                    bucket.popleft()
                if bucket:
                    self._records[normalize_id(group_id)] = bucket
            await self._enforce_group_limit_locked()

    async def _enforce_group_limit_locked(self) -> None:
        if len(self._records) <= self.config.max_cached_groups:
            return
        ordered = sorted(
            self._records.items(),
            key=lambda item: item[1][-1].timestamp if item[1] else 0,
        )
        for group_id, _ in ordered[: max(0, len(self._records) - self.config.max_cached_groups)]:
            self._records.pop(group_id, None)
