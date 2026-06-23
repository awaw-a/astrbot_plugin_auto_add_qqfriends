# QQ 好友申请助手

`astrbot_plugin_auto_add_qqfriends` 为 AstrBot 的 QQ OneBot v11（`aiocqhttp`）适配器提供两个能力：

- 自动处理可信来源的 QQ 好友申请。
- 在群临时会话或加好友后的私聊中，为本轮 LLM 请求补充有限的群聊上下文。

插件默认处于安全保守状态：`dry_run=true`、`auto_approve_enabled=false`。首次启用时不会真实同意任何好友申请。

## 支持平台

当前仅声明支持 AstrBot `aiocqhttp` 平台，也就是 OneBot v11 反向 WebSocket 适配器。主要按 NapCatQQ 常见事件结构兼容，同时尽量兼容其他 OneBot v11 实现。

开发时参考的 AstrBot 官方源码为 `AstrBotDevs/AstrBot` master：`4.26.0-beta.12`，commit `756469a39f6e27b39b98afc635ceed35f26f5c38`（2026-06-21）。本机没有安装 AstrBot 本体，因此未做真实 AstrBot/NapCat 联调。

## 事件接入方式

当前 AstrBot 官方 `aiocqhttp` 适配器会注册 `bot.on_request()`，把 OneBot request 事件转换为 `AstrMessageEvent`，并把原始事件保存在 `event.message_obj.raw_message`。插件监听 `EventMessageType.ALL`，只在原始事件满足以下条件时处理好友申请：

```text
post_type=request
request_type=friend
```

同意好友申请时通过当前事件上的 `event.bot` 调用 OneBot action：

```text
set_friend_add_request(flag=..., approve=true, remark=...)
```

如果当前 AstrBot/适配器版本没有把 request 事件送入插件流水线，插件无法收到好友申请；此时不会伪装支持，需要升级 AstrBot 或在日志中排查 raw request 是否进入。

## 低风险含义

这里的“低风险”不是 QQ 官方风控等级，只是插件本地规则判断。默认无法确认时不会自动同意，也不会自动拒绝，而是记录为 pending，等待人工处理。

主要规则包括：

- 插件总开关、真实自动同意开关、dry_run。
- 来源群白名单、群黑名单、QQ 黑白名单。
- 默认仅允许白名单群来源。
- 可通过 `get_group_member_info` 确认申请人仍在来源群。
- 验证消息非空、关键词黑名单。
- 单用户冷却、全局每小时上限、单群每小时上限。
- 启动保护时间。
- OneBot API 失败重试限制和同 flag 去重。

## 来源群判断

插件按以下顺序判断好友申请来源：

1. 原始 request 事件中的明确群字段，如 `group_id`、`source_group_id`、`from_group_id`。
2. NapCat/OneBot 扩展或嵌套字段。
3. 结构明确的验证文本，如 `群号: 123456`。
4. 在允许群中调用 `get_group_member_info` 验证申请人是否为成员。
5. 使用近期群聊缓存辅助匹配。

如果候选群不唯一，结果会标记为 `ambiguous`，不会自动同意。

## 群聊上下文

插件只缓存机器人能收到的群纯文本消息，默认不保存图片、语音、文件内容或本地路径。缓存有 TTL、最大群数、每群最大消息数、单条长度限制，并会基础脱敏 `access_token`、`Authorization`、`Cookie`、`API Key`、密码等明显敏感文本。

私聊上下文注入使用 AstrBot 的 `@filter.on_llm_request()`。如果当前 AstrBot 支持 `req.extra_user_content_parts` 和 `TextPart.mark_as_temp()`，插件会把 `<qq_group_context>` 作为临时 TextPart 注入本轮请求，不写入长期会话历史；不把动态群聊内容拼接到 system prompt。

上下文内容是不可信用户数据。注入块会明确提醒模型不要执行其中的提示词、系统指令或工具调用要求。

## 数据位置

优先使用 AstrBot 官方 `StarTools.get_data_dir()`，数据目录通常为：

```text
data/plugin_data/astrbot_plugin_auto_add_qqfriends/
```

包含：

- `processed_requests.json`
- `pending_requests.json`
- `user_group_associations.json`
- `context_cache.json`
- `rate_limits.json`

写入采用临时文件加 `os.replace` 原子替换。JSON 损坏时会改名为 `.corrupt.<timestamp>` 后重新初始化。插件不保存完整 OneBot access token，也不保存完整好友申请 `flag`；记录中只保留哈希摘要。

## 安装

把本目录放入 AstrBot 的插件目录，例如：

```text
data/plugins/astrbot_plugin_auto_add_qqfriends
```

然后在 WebUI 中启用插件并配置 `_conf_schema.json` 中的项目。运行时没有额外第三方依赖。

## 推荐首次启用步骤

1. 保持默认 `dry_run=true` 和 `auto_approve_enabled=false`。
2. 在 `allowed_group_ids` 填入可信来源群。
3. 保持 `only_allow_whitelisted_groups=true`。
4. 观察 AstrBot 日志和 `/autoqq pending`、`/autoqq recent`。
5. 确认来源判断和 pending 结果符合预期后，再设置 `auto_approve_enabled=true`。
6. 最后确认无误后再关闭 `dry_run`。

## 管理员指令

所有 `/autoqq` 子命令都限制为 AstrBot 管理员。

```text
/autoqq status
/autoqq pending
/autoqq recent
/autoqq test_source <QQ号>
/autoqq clear_context <群号>
/autoqq clear_association <QQ号>
/autoqq reload
```

`reload` 只重新读取插件持久化状态。AstrBotConfig 是否热更新由 AstrBot 管理；配置变更建议在 WebUI 重载插件。

## 重要配置

- `enabled`：总开关。
- `auto_approve_enabled`：是否允许真实自动同意，默认关闭。
- `dry_run`：演练模式，默认开启。
- `allowed_group_ids`：可信来源群。
- `blocked_group_ids`、`blocked_user_ids`：黑名单，优先级最高。
- `allowed_user_ids`：用户白名单。
- `user_whitelist_bypass_group_rule`：用户白名单是否绕过群白名单，默认不绕过。
- `require_current_group_membership`：是否要求成员关系 API 确认。
- `startup_grace_seconds`：启动保护时间。
- `global_approvals_per_hour`、`per_group_approvals_per_hour`：限流。
- `context_cache_enabled`、`context_injection_enabled`：上下文缓存和注入开关。
- `inject_only_first_private_message`：默认同一来源关联只注入一次。
- `minimum_source_confidence`：来源置信度低于该值时不自动同意、不注入。

## 常见问题

**为什么没有自动同意？**

默认不会自动同意。需要同时满足 `enabled=true`、`auto_approve_enabled=true`、`dry_run=false`，并且通过所有本地低风险规则。

**为什么 request 事件没出现？**

确认 AstrBot 使用的是 `aiocqhttp` OneBot v11 适配器，且 NapCat/OneBot 端确实发送了 request 事件。调试时可以开启 AstrBot/aiocqhttp 原始事件日志，但不要公开 access token、完整 flag 或包含敏感信息的完整事件。

**pending 里能不能直接同意？**

第一版不保存完整 flag，因此 pending 只用于审计和人工判断，不能在插件内补同意历史申请。这是为了避免长期保存敏感 token。

**会主动发送欢迎消息吗？**

第一版默认不实现欢迎消息。即使好友通过成功，也不假设 OneBot 能立即主动发起私聊。

## 已知限制

- 未在真实 AstrBot/NapCat 环境联调，本仓库只运行了纯逻辑单元测试。
- OneBot v11 不保证好友申请事件一定包含来源群；来源不明确时不会自动同意。
- 不支持非 `aiocqhttp` 平台。
- 如果适配器没有把 request 事件进入插件层，好友申请自动处理不可用。
- 上下文缓存只覆盖机器人在线且可见的群消息。

## 卸载和清理

停用插件会取消后台持久化任务并保存当前状态。卸载后如需清理数据，删除 AstrBot 数据目录下：

```text
data/plugin_data/astrbot_plugin_auto_add_qqfriends/
data/config/astrbot_plugin_auto_add_qqfriends_config.json
```

## 开发和测试

```bash
python3 -m pip install -r requirements-dev.txt
PYTHONPYCACHEPREFIX=/tmp/autoqq-pycache python3 -m py_compile main.py models/*.py services/*.py tests/*.py
pytest
```

如安装了 ruff：

```bash
ruff check .
ruff format --check .
```

## 禁止滥用

请不要把本插件用于批量加人、骚扰、绕过 QQ 风控或其他违反平台规则的行为。插件的保守默认值是为了降低误操作和隐私风险。
