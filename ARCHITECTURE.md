# Architecture

## 目标

把 Responses↔Chat Completions 协议桥提取为最小独立服务：

```text
Responses client ⟶ bridge ⟶ Chat Completions upstream (NewAPI)
```

Bridge 只做协议转换；provider 聚合与模型路由由 NewAPI 负责。

## 当前模块结构（2026-06 重构后）

```
codex_chat_bridge/
├── app.py                          # uvicorn 入口 (stable facade)
├── config.py                       # 环境变量 + _UNSET 哨兵 + get_settings()
├── models.py                       # Pydantic: ResponsesRequest, ChatCompletionsRequest, ResponsesResponse, ChatMessage
├── errors.py                       # BridgeError 层级: InvalidRequestError, UpstreamError, StreamError, UnsupportedInputItemError
├── response_semantics.py           # finish_reason → status/incomplete_details 映射 + usage 归一化
├── tool_arguments.py               # canonicalize_tool_arguments (JSON 排序/归一化)
├── inline_think_sm.py             # InlineThinkStateMachine 三态机 (detecting→reasoning→text)
├── metrics.py                      # Prometheus 指标
├── reasoning_policy.py             # canonical effort 归一化 + provider bucket 分发
│
├── protocol/                       # 协议层：SSE、会话、类型
│   ├── sse.py                      # SSE 帧解析/序列化 (extract_block, parse_sse_json_block, serialize_event, sse_done)
│   ├── session.py                  # SessionStore + SessionRecord + resolve/save/iterate
│   └── types.py                    # TypedDict: ChatMessageInput, ChatResponseInput, ResponsesInputItem, ContentPart 等
│
├── bridge_context/                  # 请求级工具上下文
│   ├── constants.py                # 工具常量 (TOOL_SEARCH_PROXY_NAME, NAMESPACE_SEP)
│   ├── models.py                   # ToolSpec 轻量数据
│   ├── naming.py                   # namespace flatten / hash / restore
│   ├── custom_tools.py             # custom tool 参数编解码 + tool_search 对象化
│   ├── context.py                  # BridgeToolContext 聚合 + tool schema 注册
│   └── builder.py                  # request input 遍历 + context 构建
│
├── responses_to_chat/              # Responses API → Chat Completions 转换
│   ├── request.py                  # request-level 组装 (reasoning/format/token 映射)
│   ├── items.py                    # input items → Chat messages 组装
│   ├── constants.py                # EXTRA_CHAT_PASSTHROUGH_FIELDS, BUILT_IN_RESPONSES_TOOLS, is_openai_o_series
│   ├── content.py                  # flatten_text_content, instruction_text, reasoning_item_text, normalize_tool_output_content
│   ├── content_mapping.py          # chat_message_content_from_response_content + iter_input_items
│   ├── media.py                    # is_safe_image_url, chat_image/audio_part_from_input_item
│   ├── tools.py                    # normalize_message_tool_calls, reasoning backfill
│   ├── message_normalization.py    # _sanitize_chat_messages, collapse_system_messages_to_head
│   ├── orphan.py                   # has_matching_call (tool output 无对应 call 检测)
│   ├── errors.py                   # UnsupportedResponsesInputItemError (→ BridgeError 子类)
│   └── __init__.py                 # convert = responses_to_chat_request (对称入口)
│
├── chat_to_responses/              # Chat Completions → Responses API 转换
│   ├── response.py                 # chat_text_to_responses (envelope 组装 + request echo)
│   ├── text.py                     # extract_reasoning_text, output_text_from_parts
│   ├── annotations.py             # extract_message_annotations, message_content_parts (annotation 合并)
│   ├── tools.py                    # chat_tool_calls_to_response_items (function/custom/tool_search)
│   ├── inline_think.py            # split_inline_think (非流式 inline 느 Extraction)
│   └── __init__.py                 # convert = chat_text_to_responses (对称入口)
│
├── stream_chat_to_responses.py     # SSE block 解析 + 事件路由 + 终止/错误分支
├── stream_responses_state.py       # 流式状态机 facade (MessageState, ReasoningState, ToolStateStore)
├── stream_state/                   # 状态机子模块
│   ├── envelope.py                 # response envelope + reasoning/usage/finish 元数据
│   ├── message.py                  # assistant message + output_text/refusal parts
│   ├── reasoning.py                # reasoning item + summary 事件
│   ├── tools.py                    # tool call 状态编排 + state store
│   ├── tool_items.py              # ToolCallState + kind 解析 + item 构造
│   └── tool_events.py             # tool SSE events 发射器
│
├── upstream.py                     # UpstreamClient facade + request lifecycle
├── upstream_transport.py          # 纯 HTTP 传输 (retry/backoff/send/cleanup)
├── upstream_compat.py             # 400 compat retry + provider_default fallback
│
└── api/                            # HTTP 边界
    ├── lifespan.py                 # FastAPI 生命周期 + BridgeError exception_handler
    ├── routes.py                   # 路由注册 + request 编排 (raise BridgeError 分支)
    ├── middleware.py                # access-log JSONL + Prometheus
    ├── concurrency.py              # asyncio.Semaphore
    ├── policy.py                   # effective-input UX guard
    └── errors.py                   # bridge_error_response() (BridgeError → JSONResponse)
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
- `UnsupportedInputItemError` → 400 (不支持的输入项)
- 裸 `BridgeError` → 自定义 status_code

所有异常统一经 `errors.py` 层级定义，由 `api/lifespan.py` 的 `add_exception_handler(BridgeError)` 捕获，不再需要 routes.py 手动构造 JSON 错误响应。

## 类型安全

- `protocol/types.py` 提供 Response→Chat 和 Chat→Response 热路径的 TypedDict（`ChatMessageInput`, `ChatResponseInput`, `ResponsesInputItem`, `ChatToolCallOutput`, `ContentPart` 等）
- 转换层函数签名已使用 TypedDict 替代 `dict[str, Any]`
- Pydantic 模型字段、upstream request rewrite、reasoning policy 中的 `dict[str, Any]` 属于协议透传本质，保持不变

## Phase 历史

### Phase 1：最小可运行桥（已完成）
- `GET /health`, `GET /v1/models`, 非流式 `/v1/responses`
- 文本型 input → messages, assistant text → Responses output_text

### Phase 2：协议完整化（已完成）
- streaming SSE, function/custom/tool_search calls, reasoning, usage, refusal, image, audio
- `previous_response_id` 会话延续, system collapse, all 15 SSE events

### Phase 3：生产化（已完成）
- systemd + /opt 持久化 + NewAPI 串联验证

### Phase 4：架构重构（2026-06 已完成）
- 删除 alias 空壳, 重命名到语义名 (media, content, tools)
- 拆分大文件 (common.py → constants + content_mapping; items.py 分出 orphan)
- InlineThinkStateMachine 独立, 对称 convert() 入口
- BridgeError 统一异常层级 + FastAPI exception_handler
- protocol/ 子包 (sse, session, types)
- TypedDict 化转换层签名
- 所有 re-export facade 删除, 直引子模块
- 135/135 测试全绿

## Policy Matrix（当前冻结）

| 层 | 职责 | 当前策略 | 典型结果 |
|---|---|---|---|
| transform parity | Responses→Chat 转换语义 | 未知 item/part 优先忽略 | unsupported item 被丢弃 |
| app UX guard | 本地拦截坏请求 | empty/blank → 400 | 避免打到上游炸 500 |
| upstream passthrough | 保留上游最终裁决 | 有效语义即转发 | mixed supported+unsupported 仍 200 |

## 协议能力矩阵

| Responses Feature | Bridge | Provider-Dependent | 备注 |
|:--|:--:|:--:|:--|
| Text | ✅ | — | 核心协议，双向完整 |
| Streaming SSE | ✅ | — | 15 种事件全覆盖 |
| input_image → image_url | ✅ | ✅ 上游需支持 image | 含 SSRF 校验 |
| Function Call | ✅ | ✅ 上游需支持 tool_calls | function/custom/tool_search |
| Namespace Tool | ✅ | — | flatten→restore roundtrip |
| Reasoning | ✅ | ✅ 上游需返回 reasoning_content | 流式/非流式均支持 |
| refusals | ✅ | — | 非流式 + content array |
| previous_response_id | ✅ | — | 内存存储, TTL 1h, deepcopy 隔离 |
| input_file / input_audio | ❌ | ✅ | 桥无映射逻辑 |
| computer_call | ❌ | ✅ | 桥无映射逻辑 |
| Hosted Tools | ⬜ | ✅ | 桥拒绝转换 built-in tool |
| MCP Tool | ⬜ | — | 经 custom tool 通道透传 |
| usage / incomplete_details | ✅ | — | 双向映射 |
| text.format → response_format | ✅ | ✅ | json_object / json_schema |
| metadata | ⬜ | — | 透传字段 |
