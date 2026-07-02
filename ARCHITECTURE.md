# Architecture

## 目标

把 Responses↔Chat Completions 协议桥提取为最小独立服务：

```text
Responses client ⟶ bridge ⟶ Chat Completions upstream (NewAPI)
```

Bridge 只做协议转换；provider 聚合与模型路由由 NewAPI 负责。

## 当前模块结构（2026-07 Phase A/B 收口后）

```
codex_chat_bridge/
├── app.py                          # uvicorn 入口 (stable facade)
├── config.py                       # 环境变量 + _UNSET 哨兵 + get_settings()
├── models.py                       # Pydantic: ResponsesRequest, ChatCompletionsRequest, ResponsesResponse, ChatMessage
├── errors.py                       # BridgeError 层级: InvalidRequestError, UpstreamError, StreamError, UnsupportedInputItemError
├── response_semantics.py           # status/incomplete 映射 + usage 归一化 + REQUEST_ECHO_FIELDS (唯一权威定义)
├── tool_arguments.py               # canonicalize_tool_arguments (JSON 排序/归一化)
├── inline_think_sm.py              # InlineThinkStateMachine 三态机 (detecting→reasoning→text)
├── metrics.py                      # Prometheus 指标 (upstream_errors_total 已接入)
├── reasoning_policy.py             # canonical effort 归一化 + provider bucket 分发 + _error_mentions
│
├── protocol/                       # 协议层：SSE、会话、类型
│   ├── sse.py                      # SSE 帧解析/序列化
│   ├── session.py                  # SessionStore + SessionRecord (last_accessed_at 续期, deepcopy 隔离)
│   └── types.py                    # TypedDict: ChatMessageInput, ChatResponseInput, ResponsesInputItem,
│                                   #   ChatToolCall, ResponsesToolCallItem, ContentPart 等
│
├── bridge_context/                 # 请求级工具上下文
│   ├── constants.py                # TOOL_SEARCH_PROXY_NAME, NAMESPACE_SEP
│   ├── models.py                   # ToolSpec 轻量数据 + nested namespace 元信息
│   ├── naming.py                   # namespace flatten / hash / restore
│   ├── nested_namespace.py         # shared nested namespace action normalize + nested_anyof params flatten
│   ├── custom_tools.py             # custom tool 参数编解码 + partial streamed input prefix 解析 + tool_search 对象化
│   ├── context.py                  # BridgeToolContext 聚合 + tool schema 注册
│   └── builder.py                  # request input 遍历 + context 构建 (tool_search_output 提前 return)
│
├── responses_to_chat/              # Responses API → Chat Completions 转换
│   ├── request.py                  # request-level 组装 (reasoning/format/token 映射)
│   ├── items.py                    # input items → Chat messages 组装 (含 _merge_reasoning_content)
│   ├── constants.py                # EXTRA_CHAT_PASSTHROUGH_FIELDS (不含 response_format), is_openai_o_series (regex)
│   ├── content.py                  # flatten_text_content, instruction_text, _join_reasoning (共享归并)
│   ├── content_mapping.py          # chat_message_content_from_response_content + iter_input_items
│   ├── media.py                    # is_safe_image_url, is_safe_audio_url, chat_image/audio_part_from_input_item
│   ├── tools.py                    # normalize_message_tool_calls, reasoning backfill (使用 _join_reasoning)
│   ├── message_normalization.py    # _sanitize_chat_messages, collapse_system_messages_to_head
│   ├── orphan.py                   # has_matching_call (tool output 无对应 call 检测)
│   ├── errors.py                   # UnsupportedResponsesInputItemError (→ BridgeError 子类)
│   └── __init__.py                 # convert = responses_to_chat_request (对称入口)
│
├── chat_to_responses/              # Chat Completions → Responses API 转换
│   ├── response.py                 # chat_text_to_responses (envelope 组装 + request echo, 使用 REQUEST_ECHO_FIELDS)
│   ├── text.py                     # extract_reasoning_text, output_text_from_parts
│   ├── annotations.py              # extract_message_annotations, message_content_parts (annotation 合并)
│   ├── tools.py                    # chat_tool_calls_to_response_items (使用 ResponsesToolCallItem 类型)
│   ├── inline_think.py             # split_inline_think, could_be_partial_think_open
│   └── __init__.py                 # convert = chat_text_to_responses (对称入口)
│
├── stream_chat_to_responses.py     # SSE block 解析 + 事件路由 + termination/error + debug logging
├── stream_responses_state.py       # 流式状态机 facade（fail/truncated finalize；assistant replay 保留 chat-side tool shape）
├── stream_state/                   # 状态机子模块
│   ├── envelope.py                 # response envelope + reasoning/usage/finish 元数据 (import REQUEST_ECHO_FIELDS)
│   ├── message.py                  # assistant message + output_text/refusal parts (annotations on added+done)
│   ├── reasoning.py                # reasoning item + summary 事件
│   ├── tools.py                    # tool call 状态编排 + state store（stable output_index by tool index；custom input incremental deltas；raw chat-side replay fields）
│   ├── tool_items.py               # ToolCallState + kind 解析 + item 构造 (tool_search_call 有稳定 id)
│   └── tool_events.py              # tool SSE events 发射器
│
├── upstream.py                     # UpstreamClient facade + request lifecycle
├── upstream_transport.py           # 纯 HTTP 传输 (retry/backoff/send/cleanup)
├── upstream_compat.py              # 400 compat retry + provider_default fallback + explicit tool_choice/thinking-mode compat
│
└── api/                            # HTTP 边界
    ├── lifespan.py                 # FastAPI 生命周期 + BridgeError exception_handler
    ├── routes.py                   # 薄 HTTP 层：路由注册 + 并发门控 (concurrency_usage 在 semaphore 内)
    ├── response_service.py         # 响应服务层：ServiceDependencies dataclass, 会话 model 回退/变更告警,
    │                               #   buffer-then-SSE 失败跳过 save, InvalidRequestError 替代 assert
    ├── middleware.py               # access-log JSONL + Prometheus
    ├── concurrency.py              # asyncio.Semaphore (reset_semaphore 用 is not None 守卫)
    ├── policy.py                   # effective-input UX guard
    └── errors.py                   # bridge_error_response() (BridgeError → JSONResponse, 含 item_type)
```

## 错误传播

```
业务层 raise InvalidRequestError / UpstreamError / BridgeError
  ↓
FastAPI exception_handler(BridgeError) 自动拦截
  ↓
bridge_error_response() → JSONResponse (正确 status_code + error body)
```

- `InvalidRequestError` → 400
- `UpstreamError` → 502
- `StreamError` → 500 (流式内部错误)
- `UnsupportedInputItemError` → 400 (不支持的输入项，含 `item_type` 字段)
- 裸 `BridgeError` → 自定义 status_code

所有异常统一经 `errors.py` 层级定义，由 `api/lifespan.py` 的 `add_exception_handler(BridgeError)` 捕获。

## 类型安全

- `protocol/types.py` 提供转换热路径 TypedDict（`ChatMessageInput`, `ChatResponseInput`, `ResponsesInputItem`, `ChatToolCall`, `ResponsesToolCallItem`, `ContentPart` 等）
- `ChatToolCall`（Chat Completions shape）和 `ResponsesToolCallItem`（Responses output-item shape）已分离，不再共用
- `api/response_service.py` 用 `ServiceDependencies` dataclass 收拢服务层依赖
- `REQUEST_ECHO_FIELDS` 唯一定义在 `response_semantics.py`，envelope.py 和 response.py 均从此导入
- Pydantic 模型字段、upstream request rewrite、reasoning policy 中的 `dict[str, Any]` 属于协议透传本质，保持不变

## Phase 历史

### Phase 1：最小可运行桥（已完成）
- `GET /health`, `GET /v1/models`, 非流式 `/v1/responses`
- 文本型 input → messages, assistant text → Responses output_text

### Phase 2：协议完整化 + 首次架构重构（已完成）
- streaming SSE, function/custom/tool_search calls, reasoning, usage, refusal, image, audio
- `previous_response_id` 会话延续, system collapse, all 15 SSE events
- 删除 alias 空壳, 重命名到语义名 (media, content, tools)
- 拆分大文件, InlineThinkStateMachine 独立, BridgeError 统一异常层级
- protocol/ 子包 (sse, session, types), TypedDict 化转换层签名
- 12 项审计问题全部修复

### Phase 3：生产化 + 二次审计（已完成）
- systemd + /opt 持久化 + NewAPI 串联验证
- 服务层抽取 (routes.py → response_service.py)
- 7 项新审计问题全部修复

### Phase 4：深度审计 + 三度修复（已完成）
- 4 P0: buffered SSE refusal/annotation, tool-call reasoning roundtrip, text .strip(), tool_search item_id
- 5 P1: stream fail 事件, annotations on added, stream_options retry, type split, session model
- 6 P2 + 1 P3: dead config/metrics, concurrency metric 时序, envelope, test cleanup
- 16 项全部修复

### Phase 5：细度审计 + 最终收口（已完成）
- 1 P0: message.py latent KeyError
- 6 P1: assert→raise, content/refusal 隔离, semaphore reset, buffer-then-SSE save guard, session TTL 续期
- 21 P2: 4 处代码去重, 3 处死代码, response_format 双写消除, o-series regex, builder quadratic fix, 等
- 4 P3: 死常量, sentinel type, events 声明位置

**截至 2026-07-02：Phase A/B 收口后的 post-closure refactor ladder 已继续推进到 stream/session fidelity 收口；当前全量测试 246 passed, 1 warning。**

## Policy Matrix（当前冻结）

| 层 | 职责 | 当前策略 | 典型结果 |
|---|---|---|---|
| transform parity | Responses→Chat 转换语义 | 未知 item/part 优先忽略 | unsupported item 被丢弃 |
| app UX guard | 本地拦截坏请求 | empty/blank → 400 | 避免打到上游炸 500 |
| upstream passthrough | 保留上游最终裁决 | 有效语义即转发 | mixed supported+unsupported 仍 200 |
| hosted tools policy | Responses 内置 hosted tool 处理 | `ignore` / `reject` / `passthrough` 可配置 | 静默跳过、直接报错或原样透传 |

## 协议能力矩阵

| Responses Feature | Bridge | Provider-Dependent | 备注 |
|:--|:--:|:--:|:--|
| Text | ✅ | — | 核心协议，双向完整 |
| Streaming SSE | ✅ | — | 15 种事件全覆盖 + fail 事件保序 |
| input_image → image_url | ✅ | ✅ 上游需支持 image | 含 SSRF 校验 |
| input_audio | ✅ | ✅ 上游需支持 audio part | 支持 URL / data 映射到 Chat `input_audio` |
| Function Call | ✅ | ✅ 上游需支持 tool_calls | function/custom/tool_search；item_id 稳定 |
| Namespace Tool | ✅ | — | flatten→restore roundtrip；nested_oneof / nested_anyof；explicit namespace `tool_choice` compat 已验证 |
| Reasoning | ✅ | ✅ 上游需返回 reasoning_content | 流式/非流式/typed tool-call 均保留 |
| refusals | ✅ | — | 非流式 + content array + session 独立存储 |
| previous_response_id | ✅ | — | 内存存储, TTL 1h + access 续期, deepcopy 隔离 |
| input_file | ❌ | ✅ | 桥无映射逻辑 |
| computer_call | ❌ | ✅ | 桥无映射逻辑 |
| Hosted Tools | ⬜ | ✅ | 行为受 `BRIDGE_UNSUPPORTED_TOOL_POLICY` 控制：ignore / reject / passthrough |
| MCP Tool | ⬜ | — | 经 custom tool 通道透传 |
| usage / incomplete_details | ✅ | — | 双向映射 |
| text.format → response_format | ✅ | ✅ | json_object / json_schema |
| metadata | ⬜ | — | 透传字段 |
| annotations | ✅ | — | added+done 事件均携带已知 annotations |
