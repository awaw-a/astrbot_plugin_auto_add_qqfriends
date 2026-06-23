from __future__ import annotations

import asyncio
import importlib
import sys
import types


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


class FakeFilter:
    class PlatformAdapterType:
        AIOCQHTTP = 1

    class EventMessageType:
        ALL = 1

    class PermissionType:
        ADMIN = 1

    @staticmethod
    def command_group(_name):
        class Group:
            @staticmethod
            def command(_sub):
                return lambda func: func

        return lambda func: Group()

    @staticmethod
    def platform_adapter_type(_value):
        return lambda func: func

    @staticmethod
    def event_message_type(_value):
        return lambda func: func

    @staticmethod
    def on_llm_request():
        return lambda func: func

    @staticmethod
    def permission_type(_value):
        return lambda func: func


class FakeStar:
    def __init__(self, context=None):
        self.context = context


class FakeStarTools:
    data_dir = None

    @classmethod
    def get_data_dir(cls, _name):
        return cls.data_dir


class FakeTextPart:
    def __init__(self, text):
        self.text = text
        self._temp = False

    def mark_as_temp(self):
        self._temp = True
        return self


def _install_astrbot_stubs(tmp_path):
    FakeStarTools.data_dir = tmp_path
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = FakeLogger()
    event_mod = types.ModuleType("astrbot.api.event")
    event_mod.AstrMessageEvent = object
    event_mod.filter = FakeFilter
    star_mod = types.ModuleType("astrbot.api.star")
    star_mod.Context = object
    star_mod.Star = FakeStar
    star_mod.StarTools = FakeStarTools
    star_mod.register = lambda *args, **kwargs: lambda cls: cls
    message_mod = types.ModuleType("astrbot.core.agent.message")
    message_mod.TextPart = FakeTextPart
    sys.modules["astrbot"] = types.ModuleType("astrbot")
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.core"] = types.ModuleType("astrbot.core")
    sys.modules["astrbot.core.agent"] = types.ModuleType("astrbot.core.agent")
    sys.modules["astrbot.core.agent.message"] = message_mod


class FakeBot:
    def __init__(self):
        self.actions = []

    async def call_action(self, action, **params):
        self.actions.append((action, params))
        if action == "get_group_member_info":
            return {"user_id": params["user_id"]}
        if action == "set_friend_add_request":
            return {"status": "ok"}
        return {}


class FakeEvent:
    def __init__(self, bot):
        self.bot = bot

    def get_self_id(self):
        return "999"


def _import_main(tmp_path):
    _install_astrbot_stubs(tmp_path)
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def _config(dry_run):
    return {
        "auto_approve_enabled": True,
        "dry_run": dry_run,
        "allowed_group_ids": ["10001"],
    }


def _raw(flag="flag-1"):
    return {
        "post_type": "request",
        "request_type": "friend",
        "user_id": "42",
        "comment": "hi",
        "flag": flag,
        "group_id": "10001",
        "self_id": "999",
        "time": 100,
    }


def test_dry_run_does_not_call_approve_api(tmp_path):
    main = _import_main(tmp_path)
    plugin = main.AutoAddQQFriendsPlugin(context=None, config=_config(dry_run=True))
    bot = FakeBot()
    asyncio.run(plugin._handle_friend_request(FakeEvent(bot), _raw()))
    actions = [name for name, _ in bot.actions]
    assert "set_friend_add_request" not in actions
    assert plugin.processed_records[-1].result == "dry_run"


def test_duplicate_flag_is_not_processed_twice(tmp_path):
    main = _import_main(tmp_path)
    plugin = main.AutoAddQQFriendsPlugin(context=None, config=_config(dry_run=False))
    bot = FakeBot()
    event = FakeEvent(bot)
    asyncio.run(plugin._handle_friend_request(event, _raw("same-flag")))
    asyncio.run(plugin._handle_friend_request(event, _raw("same-flag")))
    approve_calls = [
        name for name, _ in bot.actions if name == "set_friend_add_request"
    ]
    assert approve_calls == ["set_friend_add_request"]
