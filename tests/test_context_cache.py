from __future__ import annotations

import asyncio

from models.records import GroupMessageRecord, UserGroupAssociation, now_ts
from services.context_cache import ContextCache, ContextCacheConfig


def _record(
    group_id: str = "10001",
    sender_id: str = "42",
    text: str = "hello",
    ts: float | None = None,
) -> GroupMessageRecord:
    return GroupMessageRecord(
        group_id=group_id,
        group_name="群",
        sender_id=sender_id,
        sender_name=f"u{sender_id}",
        text=text,
        timestamp=ts if ts is not None else now_ts(),
        is_bot=False,
        message_id=f"m-{sender_id}-{text}",
    )


def test_expired_context_is_cleaned():
    cache = ContextCache(ContextCacheConfig(context_ttl_seconds=60))
    asyncio.run(cache.add_message(_record(ts=100)))
    removed = asyncio.run(cache.cleanup_expired(now=200))
    stats = asyncio.run(cache.stats())
    assert removed == 1
    assert stats["messages"] == 0


def test_context_limits_message_count_and_length():
    cache = ContextCache(
        ContextCacheConfig(max_messages_per_group=2, max_message_length=5)
    )
    asyncio.run(cache.add_message(_record(text="abcdef")))
    asyncio.run(cache.add_message(_record(text="second")))
    asyncio.run(cache.add_message(_record(text="third")))
    data = asyncio.run(cache.to_json())
    rows = data["groups"]["10001"]
    assert len(rows) == 2
    assert rows[0]["text"] == "seco…"


def test_sensitive_text_is_redacted():
    cache = ContextCache(ContextCacheConfig())
    asyncio.run(cache.add_message(_record(text="access_token=abcdef Cookie: sid=123")))
    data = asyncio.run(cache.to_json())
    text = data["groups"]["10001"][0]["text"]
    assert "abcdef" not in text
    assert "[REDACTED]" in text


def test_user_group_association_expiry():
    assoc = UserGroupAssociation(
        user_id="42",
        group_id="10001",
        detection_method="test",
        confidence=0.9,
        approved_at=100,
        expires_at=150,
    )
    assert not assoc.is_expired(149)
    assert assoc.is_expired(150)


def test_context_selection_prioritizes_user_messages():
    cache = ContextCache(ContextCacheConfig(max_messages_per_group=10))
    base = now_ts()
    asyncio.run(cache.add_message(_record(sender_id="7", text="before", ts=base)))
    asyncio.run(cache.add_message(_record(sender_id="42", text="own", ts=base + 1)))
    asyncio.run(cache.add_message(_record(sender_id="8", text="after", ts=base + 2)))
    asyncio.run(cache.add_message(_record(sender_id="9", text="later", ts=base + 3)))
    selected = asyncio.run(
        cache.select_context(
            user_id="42",
            group_id="10001",
            max_messages=3,
            before=1,
            after=1,
            time_window_seconds=1000,
        )
    )
    assert [item.text for item in selected] == ["before", "own", "after"]
