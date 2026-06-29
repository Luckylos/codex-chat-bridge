# codex-chat-bridge 完整重构基准文档

> **生成时间**: 2026-06-29  
> **基准版本**: 当前 working tree（含 #1-#10 + 3 项"更好"改进）  
> **测试状态**: 135/135 全绿  
> **目标**: 在新窗口中基于此基准进行完整重构，消除修补痕迹，实现逻辑闭环，提升架构和代码质量

---

## 1. 项目定位

codex-chat-bridge 是一个 **thin single-upstream protocol-conversion relay**：

- 职责：Responses API ↔ Chat Completions API 双向协议转换
- 上游：单一 NewAPI 实例（127.0.0.1:3000）
- 不做的事：多上游路由、模型聚合、provider 能力中心、未知字段透传、SDK-shape extra_body
- 参考 baseline：本地 cc-switch-cli 5.8.4

---

## 2. 当前文件结构（3,500 行 Python）

```
codex_chat_bridge/
├── __init__.py                          (5)
├── app.py                              (59)   FastAPI 应用入口
├── config.py                           (128)  环境变量配置
├── models.py                           (132)  Pydantic 请求/响应模型
├── metrics.py                          (32)   Prometheus 指标
├── sse_utils.py                        (96)   SSE 事件序列化
├── session_store.py                    (193)  会话存储（previous_response_id 链式）
├── tool_context.py                     (31)   兼容别名模块
├── response_semantics.py               (71)   共享响应语义（status/usage/canonicalize）
├── reasoning_policy.py                 (202)  reasoning effort 规范化 + 方言编码
├── upstream.py                         (203)  上游 HTTP 客户端
├── upstream_transport.py               (74)   上游传输层
├── upstream_compat.py                  (111)  400 兼容性降级重试
├── transform_chat_to_responses.py      (3)    alias
├── transform_responses_to_chat.py      (5)    alias
│
├── api/
│   ├── __init__.py                     (9)
│   ├── routes.py                       (205)  /v1/responses 端点（核心 handler）
│   ├── concurrency.py                   (20)   并发控制
│   ├── errors.py                       (26)   错误响应格式
│   ├── lifespan.py                      (55)   应用生命周期
│   ├── middleware.py                    (57)   中间件
│   └── policy.py                       (45)   请求校验策略
│
├── bridge_context/
│   ├── __init__.py                     (30)   re-export
│   ├── builder.py                      (50)   tool context 构建
│   ├── constants.py                    (5)    常量
│   ├── context.py                      (154)  BridgeToolContext 主体
│   ├── custom_tools.py                 (38)   custom tool 代理
│   ├── models.py                       (10)   内部模型
│   └── naming.py                       (43)   命名映射
│
├── chat_to_responses/                  ← Chat→Responses 方向
│   ├── __init__.py                     (10)
│   ├── common.py                       (93)   extract_reasoning_text, message_content_parts, annotations 合并
│   ├── inline_think.py                 (68)   split_inline_think, could_be_partial_think_open
│   ├── response.py                     (96)   非流式 chat_text_to_responses + request echo
│   └── tools.py                        (72)   chat_tool_calls_to_response_items
│
├── responses_to_chat/                  ← Responses→Chat 方向
│   ├── __init__.py                     (7)
│   ├── common.py                       (125)  chat_message_content_from_response_content, input_audio
│   ├── content_helpers.py              (73)   flatten_text_content, instruction_text, reasoning_item_text
│   ├── errors.py                       (15)   UnsupportedResponsesInputItemError
│   ├── image_security.py               (102)  media URL SSRF 安全 + chat_audio_part_from_input_item
│   ├── items.py                        (244)  append_input_items_as_chat_messages（核心转换）
│   ├── message_normalization.py        (91)   _sanitize_chat_messages, collapse_system_messages_to_head
│   ├── request.py                      (101)  responses_to_chat_request（主入口）
│   └── tool_helpers.py                 (68)   normalize_message_tool_calls, reasoning backfill
│
└── stream_state/                       ← 流式状态机
    ├── __init__.py                     (11)
    ├── envelope.py                     (93)   ResponseEnvelopeState（output index 分配, request echo）
    ├── message.py                      (168)  MessageState（text/refusal/annotations + 三态 inline think）
    ├── reasoning.py                    (107)  ReasoningState（显式 reasoning delta）
    ├── tools.py                        (90)   ToolStateStore（tool call delta 累积）
    ├── tool_items.py                   (104)  ToolCallState, ToolKind, build_*_item
    └── tool_events.py                  (66)   SSE 事件工厂

stream_chat_to_responses.py            (190)  流式 Chat→Responses 主循环
stream_responses_state.py              (213)  ResponsesStreamState（总状态 + finalize/fail）
```

---

## 3. 已实现功能清单

### 3.1 协议转换（双向）

| 功能 | 方向 | 描述 |
|------|------|------|
| text↔output\_text | 双向 | 纯文本、结构化 content list |
| reasoning content | 双向 | 显式字段 + inline `比think比` fallback |
| refusal | 双向 | top-level + content list 内 |
| function\_call / function\_call\_output | 双向 | 含 namespace 工具拆名 |
| custom\_tool\_call / custom\_tool\_call\_output | 双向 | input↔arguments 互转 |
| tool\_search\_call / tool\_search\_output | 双向 | execution=client 透传 |
| input\_image | Responses→Chat | SSRF URL 安全检查 |
| input\_audio | Responses→Chat | URL + base64，SSRF 安全 |
| input\_text / latest\_reminder | Responses→Chat | → user message |
| message role 映射 | 双向 | system/developer→system, user→user, assistant→assistant |
| instructions | Responses→Chat | → leading system message |
| reasoning effort | Responses→Chat | 规范化 + 方言编码（effort\_only/thinking\_only/thinking\_with\_effort） |
| response\_format / text.format | Responses→Chat | 结构化输出映射 |
| anonymous input string | Responses→Chat | "hello" → user message |
| parallel tool\_calls | Chat→Responses | 每个 tool\_call 独立 output item |
| annotations | Chat→Responses | message-level + part-level 合并去重 |
| orphan tool output | Responses→Chat | call\_id 不匹配时降级 user 消息 |
| empty assistant normalization | Responses→Chat | content="" 保留而非删除 |

### 3.2 请求处理

| 功能 | 描述 |
|------|------|
| request echo 回填 | 10 个字段回填到 Responses 响应体 |
| finish\_reason→status | tool\_calls→in\_progress, length/content\_filter→incomplete |
| incomplete\_details | max\_output\_tokens / content\_filter |
| n\>1 多 choice | 取第一个 + warn 日志 |
| model 必填 | 缺失返回 400 missing\_model |
| previous\_response\_id | 会话链式追踪（session\_store） |
| 400 兼容性降级 | reasoning 方言出错时重试（effort ladder） |

### 3.3 流式（SSE）

| 功能 | 描述 |
|------|------|
| response.created / in\_progress | 起始事件 |
| output\_item.added / done | message + function\_call items |
| output\_text.delta / done | 文本增量 |
| reasoning\_summary\_text.delta / done | 推理增量 |
| function\_call\_arguments.delta / done | 工具参数增量 |
| custom\_tool\_call\_input.delta / done | 自定义工具输入增量 |
| refusal part | 拒绝部分增量 |
| response.completed / failed | 终止事件 |
| inline think 三态状态机 | DETECTING→REASONING→TEXT |
| failed 保留已完成 output | 中断不丢失 |
| UTF-8 安全拼接 | 跨 chunk 边界 |
| 上游失败→SSE error | 不中断 HTTP stream |

---

## 4. 修补痕迹清单（重构目标）

### 4.1 架构层面

| 问题 | 位置 | 描述 |
|------|------|------|
| **方向不对称** | chat\_to\_responses/ vs responses\_to\_chat/ | Chat→Responses 方向分了 4 个文件，但 Responses→Chat 方向堆了 8 个文件且 `items.py` 单文件 244 行、职责过重 |
| **transform 别名模块** | transform\_chat\_to\_responses.py, transform\_responses\_to\_chat.py | 空壳 alias，历史残留，应删除 |
| **tool\_context.py 兼容别名** | tool\_context.py | 指向 bridge\_context 的 alias，应合并或删除 |
| **common.py 过度重导出** | responses\_to\_chat/common.py | 既是 re-export hub 又含业务逻辑（chat\_message\_content\_from\_response\_content 是 70 行巨型函数） |
| **stream 成为上帝模块** | stream\_chat\_to\_responses.py (190) + stream\_responses\_state.py (213) | 两个 200 行文件承担了过多职责，且互有状态交叉 |

### 4.2 代码层面

| 问题 | 位置 | 描述 |
|------|------|------|
| **inline think 三态戳进 MessageState** | stream\_state/message.py:15-20 | `_inline_think_phase` / `_inline_think_buffer` 混入 Message 状态，语义应独立为 InlineThinkState |
| **push\_content\_delta 是 80 行巨型方法** | stream\_responses\_state.py | 含三态分支 + flush 逻辑，应拆为 InlineThinkStateMachine 独立类 |
| **image\_security.py 名不副实** | responses\_to\_chat/image\_security.py | 现在包含 `chat_audio_part_from_input_item` 和 `_is_safe_media_url`，文件名应更名 |
| **annotations 合并逻辑内联** | chat\_to\_responses/common.py:52-63 | `_extract_message_annotations` + 合并去重逻辑内联在 `message_content_parts`，应独立为 annotation 模块 |
| **orphan detection 在 items.py 内联** | responses\_to\_chat/items.py:179-207 | `has_matching_call` 检测逻辑 20 行内联，应抽取为独立 helper |
| **routes.py 构造 original\_request 硬编码字段** | api/routes.py | 10 个字段手动拼 dict，应从 models 自动生成 |
| **`_sanitize_chat_messages` 中空 assistant 分支** | message\_normalization.py:50 | 新增的 `content=""` 分支与原 Step 1 逻辑交错，意图不清 |
| **response\_semantics.py 职责扩散** | response\_semantics.py | 原来只有 usage+status，现在多了 `canonicalize_tool_arguments` + `incomplete_reason_from_finish_reason`，应拆分 |
| **`from __future__ import annotations` 冗余** | image\_security.py | 重复了两遍 |

### 4.3 一致性问题

| 问题 | 描述 |
|------|------|
| **命名风格不统一** | chat\_to\_responses 用 `chat_text_to_responses`，但 responses\_to\_chat 用 `responses_to_chat_request`——主函数命名不对称 |
| **error 处理不一致** | 非/流式路径的 error propagation 路径不同：非流式直接 raise，流式走 SSE error event |
| **类型注解风格混杂** | 部分用 `dict[str, Any]`，部分用 `Value`（Rust 风格残留），部分用 `Optional` |
| **测试文件命名不统一** | `test_better_than_cpp.py` 是临时名称，其他按功能域命名 |

---

## 5. 目标架构

### 5.1 目录结构（建议）

```
codex_chat_bridge/
├── app.py                          # FastAPI 入口（不变）
├── config.py                       # 环境变量（不变）
│
├── protocol/
│   ├── __init__.py
│   ├── models.py                   # 统一请求/响应 Pydantic 模型
│   ├── response_semantics.py       # 最小：status / incomplete_details 映射
│   ├── sse.py                      # SSE 事件序列化（原 sse_utils）
│   └── session.py                  # 会话存储（原 session_store）
│
├── direction/
│   ├── __init__.py
│   ├── chat_to_responses/          # Chat → Responses
│   │   ├── __init__.py             # 主入口：convert()
│   │   ├── text.py                 # 文本 + reasoning + refusal
│   │   ├── tools.py                # tool call 转换
│   │   ├── annotations.py         # annotation 提取/合并
│   │   └── inline_think.py         # ◁think▷ 拆分逻辑
│   │
│   └── responses_to_chat/          # Responses → Chat
│       ├── __init__.py             # 主入口：convert()
│       ├── items.py                # item 分发（slimmed down）
│       ├── content.py              # content 结构扁平化
│       ├── media.py                # image + audio 处理 + SSRF 安全
│       ├── messages.py             # message 归一化 + collapse
│       └── tools.py                # tool call 转换
│
├── stream/
│   ├── __init__.py
│   ├── state.py                    # ResponsesStreamState（重构后）
│   ├── inline_think_sm.py         # InlineThinkStateMachine（独立状态机）
│   ├── message.py                  # MessageState（纯文本/annotation）
│   ├── reasoning.py               # ReasoningState
│   ├── tools.py                    # ToolStateStore
│   └── events.py                   # SSE 事件工厂
│
├── bridge_context/                 # 工具上下文（基本不变）
│   ├── __init__.py
│   ├── context.py
│   ├── custom_tools.py
│   ├── naming.py
│   └── builder.py
│
├── upstream/                       # 上游交互
│   ├── __init__.py
│   ├── client.py                   # HTTP 客户端
│   ├── transport.py                # 传输层
│   └── compat.py                   # 400 兼容性降级
│
├── reasoning/                      # Reasoning 方言
│   ├── __init__.py
│   ├── policy.py                   # effort 规范化
│   └── dialect.py                  # 方言编码器
│
└── api/
    ├── __init__.py
    ├── routes.py                   # 端点 handler（精简）
    ├── policy.py                   # 请求校验
    ├── errors.py
    ├── middleware.py
    └── lifespan.py
```

### 5.2 设计原则

1. **方向对称**：`chat_to_responses.convert()` / `responses_to_chat.convert()` 主入口对称
2. **单一职责**：items.py 拆分为 items（分发）+ content（结构）+ media（媒体）+ tools（工具）
3. **状态机独立**：InlineThinkStateMachine 从 MessageState 中抽出
4. **命名一致**：`_from_finish_reason` 系列、`_to_response_item` 系列
5. **type-safe**：消灭裸 `dict[str, Any]`，关键路径用 TypedDict 或 dataclass
6. **error 路径统一**：非流式 / 流式 error 通过同一个 error policy 走
7. **annotation 独立模块**：提取 + 合并 + 去重逻辑独立文件
8. **media 安全统一**：image/audio 统一走 media.py 的 SSRF 检查

### 5.3 重构约束

- **功能等价**：重构后 135 个现有测试必须全绿（允许重命名/移动）
- **不引入新功能**：此次重构纯粹是架构和代码质量
- **不改变外部行为**：API 端点、SSE 事件序列、error 格式不变
- **渐进式**：可按模块分批重构，每批后全量单测必须绿

---

## 6. 当前各模块关键逻辑速查

### 6.1 `chat_to_responses/response.py` — 非流式 Chat→Responses

```
chat_text_to_responses(chat_body, fallback_model, tool_context, original_request)
  ├── extract_reasoning_text(message)          → reasoning item
  ├── message_content_parts(message)           → message item with annotations
  ├── chat_tool_calls_to_response_items()      → function_call items
  ├── response_status_from_finish_reason()      → status field
  ├── incomplete_reason_from_finish_reason()    → incomplete_details
  └── _echo_request_fields()                    → request echo
```

### 6.2 `responses_to_chat/items.py` — 核心 item 分发

```
append_input_items_as_chat_messages(payload, messages, tool_context)
  ├── _pending_text buffer → user message on flush
  ├── function_call        → pending_tool_calls (accumulate, then flush as assistant)
  ├── custom_tool_call     → pending_tool_calls (name restore via tool_context)
  ├── tool_search_call     → pending_tool_calls
  ├── function_call_output → tool message (or user if orphan)
  ├── custom_tool_call_output → tool message (or user if orphan)
  ├── tool_search_output   → tool message (or user if orphan)
  ├── reasoning            → pending_reasoning buffer
  ├── reasoning (summary)  → reasoning_content on assistant
  ├── input_image          → user message (SSRF check)
  ├── input_audio          → user message (SSRF check)
  ├── latest_reminder      → user text
  └── generic role/content → ChatMessage with role mapping
```

### 6.3 `stream_responses_state.py` — 流式总状态

```
ResponsesStreamState
  ├── envelope: ResponseEnvelopeState    (index alloc, request echo)
  ├── message: MessageState              (text, refusal, annotations, inline think)
  ├── reasoning: ReasoningState          (explicit reasoning delta)
  ├── tools: ToolStateStore               (tool calls)
  │
  ├── push_content_delta()              ← 三态 inline think 状态机
  │   ├── PHASE_DETECTING → prefix check → REASONING or TEXT
  │   ├── PHASE_REASONING → accumulate, flush on close tag
  │   └── PHASE_TEXT      → normal text delta
  │
  ├── finalize()                        → all sub-module finalize + completed event
  └── fail()                            → sub-module finalize + failed event (preserves completed items)
```

### 6.4 `response_semantics.py` — 共享语义

```
response_status_from_finish_reason(reason) → "completed" | "in_progress" | "incomplete"
incomplete_reason_from_finish_reason(reason) → {"reason": "..."} | None
map_chat_usage(usage) → {input_tokens, output_tokens, total_tokens}
canonicalize_tool_arguments(arguments) → str  # JSON canonicalize
```

### 6.5 `reasoning_policy.py` — Reasoning 方言

```
normalize_canonical_reasoning_effort(effort) → "none" | "high" | "xhigh" | "unspecified"
encode_reasoning_for_upstream(effort, dialect) → dict  # {reasoning_effort: ...} / {thinking: ...}
```

四种 dialect: `effort_only`, `thinking_only`, `thinking_with_effort`, `provider_default`

---

## 7. 测试矩阵

| 测试文件 | 行数 | 覆盖域 |
|----------|------|--------|
| test\_multiturn\_tool\_roundtrip.py | 597 | 多轮 tool 往返，message/content/refusal/annotation |
| test\_request\_validation\_semantics.py | 469 | 请求校验，400 错误码，空输入 |
| test\_upstream\_retry.py | 253 | 400 兼容性降级重试 |
| test\_tool\_search\_call.py | 293 | tool\_search 往返 |
| test\_reasoning\_policy.py | 199 | effort 规范化 + 方言编码 |
| test\_custom\_tool\_call.py | 196 | custom tool 往返 |
| test\_better\_than\_cpp.py | 106 | annotations 合并、空 assistant、orphan |
| test\_session\_store.py | 175 | 会话链接 |
| test\_image\_security.py | 77 | SSRF 安全 |
| test\_streaming\_failure\_semantics.py | 46 | 流式失败 |
| test\_streaming\_content\_refusal\_semantics.py | 102 | 流式 content/refusal |
| test\_response\_semantics.py | 67 | status/usage 映射 |
| test\_upstream\_urls.py | 77 | URL 构造 |

**总计 135 个测试，全绿**

---

## 8. 重构执行指南

### Phase 1: 结构整理（低风险）

1. 删除 `transform_chat_to_responses.py`、`transform_responses_to_chat.py`、`tool_context.py` 三个 alias
2. 重命名 `image_security.py` → `media_security.py`
3. 重命名 `test_better_than_cpp.py` → `test_protocol_improvements.py`
4. 修复 `from __future__ import annotations` 双重导入 (image_security.py)
5. 统一主入口命名：`chat_to_responses.convert()` / `responses_to_chat.convert()`

### Phase 2: 模块拆分（中风险）

1. `responses_to_chat/items.py` (244行) → items.py + content.py + media.py + tools.py
2. `chat_to_responses/common.py` (93行) → text.py + annotations.py（保留 inline_think.py）
3. `stream_responses_state.py` 的 `push_content_delta()` → 独立 InlineThinkStateMachine

### Phase 3: 类型与一致性（中风险）

1. 关键路径 TypedDict 化（ChatChunk, ResponsesOutputItem, SSEEvent 等）
2. 消灭裸 `dict[str, Any]` 在转换函数签名中
3. 统一 error propagation：定义 `BridgeError` 层级，非流式 / 流式共享

### Phase 4: 架构整理（高价值）

1. `response_semantics.py` 拆分：status/usage 保留，tool_arguments 独立
2. `routes.py` 精简：`original_request` 构造从 models 自动生成
3. `sse_utils.py` 合并入 `protocol/sse.py`

### 每步验证

- 全量 `python -m unittest discover -s tests` 必须 135 绿
- 无新 lint error
- 服务重启 + health check

---

## 9. 关键约束与边界

- **单上游架构不变**：NewAPI 是唯一上游
- **Responses API 是对外合同**：SSE 事件序列、error 格式、output 结构不变
- **cc-switch-cli 5.8.4 是兼容 baseline**：行为差异以此为判定
- **reasoning 方言四桶不变**：openai\_like / deepseek / glm / kimi
- **model 必填不变**：缺失返回 400 missing\_model
- **不引入虚拟生命周期事件**：safety\_check / truncation 等
- **不引入多上游路由**：桥定位不变

---

## 10. 当前服务状态

- 服务名：`codex-chat-bridge.service`
- 监听：`127.0.0.1:18090`
- 上游：`127.0.0.1:3000`（NewAPI）
- 运行用户：systemd 托管
- 备份：`/opt/codex-chat-bridge/backups/20260629-161552/`
