from __future__ import annotations

import asyncio

from models.records import FriendRequest
from services.source_detector import SourceDetector


class FakeBridge:
    def __init__(self, memberships: dict[tuple[str, str], bool | None]):
        self.memberships = memberships

    async def is_group_member(self, group_id: str, user_id: str) -> bool | None:
        return self.memberships.get((str(group_id), str(user_id)))


def test_explicit_group_field_is_preferred():
    request = FriendRequest.from_raw(
        {
            "user_id": 42,
            "post_type": "request",
            "request_type": "friend",
            "group_id": 10001,
        }
    )
    bridge = FakeBridge({("10001", "42"): True})
    result = asyncio.run(SourceDetector(["10001"]).detect(request, bridge=bridge))
    assert result.group_id == "10001"
    assert result.detection_method == "explicit_event_field"
    assert result.member_confirmed is True


def test_multiple_candidate_groups_are_ambiguous():
    request = FriendRequest.from_raw(
        {"user_id": "42", "post_type": "request", "request_type": "friend"}
    )
    bridge = FakeBridge({("10001", "42"): True, ("10002", "42"): True})
    result = asyncio.run(
        SourceDetector(["10001", "10002"]).detect(request, bridge=bridge)
    )
    assert result.group_id is None
    assert result.detection_method == "ambiguous"
    assert result.candidate_groups == ["10001", "10002"]


def test_structured_comment_group_is_parsed_but_not_fuzzy_text():
    detector = SourceDetector(["10001"])
    structured = FriendRequest(user_id="42", comment="来源群: 10001")
    fuzzy = FriendRequest(user_id="42", comment="我是从群里来的")
    result = asyncio.run(detector.detect(structured, require_membership=False))
    fuzzy_result = asyncio.run(detector.detect(fuzzy, require_membership=False))
    assert result.group_id == "10001"
    assert result.detection_method == "structured_comment"
    assert fuzzy_result.group_id is None

