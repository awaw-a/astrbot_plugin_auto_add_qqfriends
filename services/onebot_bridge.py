from __future__ import annotations

import asyncio
import inspect
from typing import Any

try:  # pragma: no cover
    from ..models.records import normalize_id
except ImportError:  # pragma: no cover
    from models.records import normalize_id


def extract_raw_event(event: Any) -> dict[str, Any]:
    message_obj = getattr(event, "message_obj", None)
    raw = getattr(message_obj, "raw_message", None)
    if raw is None:
        raw = getattr(event, "raw_message", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return dict(raw)
    except Exception:
        data = getattr(raw, "__dict__", None)
        return dict(data) if isinstance(data, dict) else {}


class OneBotBridge:
    def __init__(self, client: Any, self_id: str = "") -> None:
        self.client = client
        self.self_id = normalize_id(self_id)

    @classmethod
    def from_event(cls, event: Any) -> "OneBotBridge | None":
        client = (
            getattr(event, "bot", None)
            or getattr(event, "client", None)
            or getattr(event, "adapter", None)
        )
        if client is None:
            return None
        self_id = ""
        try:
            self_id = event.get_self_id()
        except Exception:
            self_id = ""
        return cls(client, self_id=self_id)

    async def approve_friend_request(
        self,
        flag: str,
        approve: bool = True,
        remark: str = "",
        retry_count: int = 1,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(max(1, retry_count + 1)):
            try:
                params: dict[str, Any] = {"flag": flag, "approve": approve}
                if remark:
                    params["remark"] = remark
                return await self.call_action("set_friend_add_request", **params)
            except Exception as exc:
                last_error = exc
                if attempt >= retry_count:
                    break
                await asyncio.sleep(min(2.0, 0.5 * (attempt + 1)))
        if last_error:
            raise last_error
        return None

    async def is_group_member(self, group_id: str, user_id: str) -> bool | None:
        try:
            payload = await self.call_action(
                "get_group_member_info",
                group_id=int(group_id) if str(group_id).isdigit() else group_id,
                user_id=int(user_id) if str(user_id).isdigit() else user_id,
                no_cache=False,
            )
            if isinstance(payload, dict):
                returned_user = normalize_id(payload.get("user_id"))
                return not returned_user or returned_user == normalize_id(user_id)
            return payload is not None
        except Exception:
            return None

    async def get_group_name(self, group_id: str) -> str:
        try:
            payload = await self.call_action(
                "get_group_info",
                group_id=int(group_id) if str(group_id).isdigit() else group_id,
                no_cache=True,
            )
            if isinstance(payload, dict):
                return str(payload.get("group_name") or "")
        except Exception:
            return ""
        return ""

    async def call_action(self, action: str, **params: Any) -> Any:
        if self.self_id and "self_id" not in params:
            params["self_id"] = self.self_id
        call_action = getattr(self.client, "call_action", None)
        if callable(call_action):
            result = call_action(action, **params)
            if inspect.isawaitable(result):
                return await result
            return result
        method = getattr(self.client, action, None)
        if callable(method):
            result = method(**params)
            if inspect.isawaitable(result):
                return await result
            return result
        raise AttributeError(f"OneBot client does not support action {action}")
