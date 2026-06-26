# Architecture

## 目标

把代码聊相关协议桥提取为最小独立服务：

```text
Responses client -> bridge -> Chat Completions upstream
```

## 为什么单独提取

原 cc-switch 项目包含大量与本目标无关的内容：

- 本地 CLI 工具检测
- `start codex` / `which::which("codex")`
- provider 管理、SQLite、failover、visible apps
- Claude / Gemini / Hermes 多应用运行时

而你当前要的只是：

1. 入站接受 `/v1/responses`
2. 请求改写成 `/v1/chat/completions`
3. 上游响应再改写回 Responses API

## 提取源文件映射

### 需要重点复用/翻译的源码

- `src-tauri/src/proxy/providers/transform_codex_chat.rs`
  - `responses_to_chat_completions_with_reasoning(...)`
  - 请求体、tool schema、reasoning、usage 相关映射
- `src-tauri/src/proxy/providers/streaming_codex_chat.rs`
  - Chat Completions SSE -> Responses SSE
- `src-tauri/src/proxy/providers/codex.rs`
  - `should_convert_codex_responses_to_chat(...)`
  - `codex_provider_uses_chat_completions(...)`
- `src-tauri/src/proxy/response.rs`
  - buffered response 包装逻辑
- `src-tauri/src/proxy/handlers.rs`
  - `/v1/responses` 路由入口与响应分发
- `src-tauri/src/proxy/forwarder/request_builder.rs`
  - 转发前 endpoint/body/auth 处理

### 明确不提取

- `src-tauri/src/cli/codex_temp_launch.rs`
- `src-tauri/src/cli/commands/start.rs`
- `src-tauri/src/services/local_env_check.rs`
- `src-tauri/src/services/visible_apps.rs`
- daemon / tray / TUI / provider DB

## 新项目模块边界

### `config.py`
- 环境变量读取
- NewAPI 单上游兼容入口
- 上游 base_url / api_key / timeout / public base_url

### `models.py`
- Responses 请求/响应的最小结构
- Chat Completions 请求/响应的最小结构
- 错误对象结构

### `bridge_context/`
- request-scoped bridge context 子模块目录
- `constants.py`：tool 常量与命名边界
- `models.py`：`ToolSpec` 等轻量上下文数据结构
- `naming.py`：namespace flatten / canonical JSON / hash 命名辅助
- `custom_tools.py`：custom tool 参数编码/解码与 tool_search 参数对象化
- `context.py`：`BridgeToolContext` 聚合与 tool schema 注册
- `builder.py`：request input 遍历、`tool_search_output` 递归注入、context 构建

### `tool_context.py`
- 仅保留稳定门面导出，避免测试、流式状态机与外部调用方 import 路径漂移
- 实际 request-scoped context 逻辑已下沉到 `bridge_context/` 子模块

### `responses_to_chat/`
- 请求转换子模块目录
- `common.py`：文本/content/tool-call reasoning/system collapse 等共享语义
- `items.py`：Responses input items -> Chat messages 组装
- `request.py`：request-level 组装、response_format/reasoning/max token 映射
- `errors.py`：显式输入项错误类型

### `transform_responses_to_chat.py`
- 仅保留稳定门面导出，避免外部 import 路径漂移
- 实际请求转换逻辑已下沉到 `responses_to_chat/` 子模块

### `chat_to_responses/`
- 非流式 Chat JSON -> Responses 恢复子模块目录
- `common.py`：reasoning/content/refusal/output_text 抽取
- `tools.py`：tool call 恢复与 tool family 分流
- `response.py`：Responses envelope 组装与 usage/status 落盘

### `transform_chat_to_responses.py`
- 仅保留稳定门面导出，避免测试与调用方 import 路径漂移
- 实际非流式恢复逻辑已下沉到 `chat_to_responses/` 子模块

### `stream_chat_to_responses.py`
- 仅保留 SSE block 解析、事件路由、终止/错误分支控制
- 调用 `stream_responses_state.py` 门面层推进 Responses SSE 状态机

### `stream_responses_state.py`
- 作为流式状态机门面层，对外暴露稳定接口
- 组装内部 `stream_state/` 模块，不再承载全部细节实现

### `stream_state/envelope.py`
- `response` envelope 生命周期
- `reasoning` item / summary 事件
- `usage` / `created_at` / `finish_reason` 元数据与 completed item 聚合

### `stream_state/message.py`
- assistant `message` item 生命周期
- `output_text` / `refusal` content parts
- `content_index` 与最终 `message.content[]` 收口

### `stream_state/tools.py`
- 作为 tool call 状态编排层
- 维护 request-scope tool call state store
- 调度 item builder / event emitter / completion 收口

### `stream_state/tool_items.py`
- `ToolCallState` 数据结构
- tool kind 解析（function/custom/tool_search）
- in-progress / completed item 构造

### `stream_state/tool_events.py`
- tool 相关 SSE event 发射器
- `function_call_arguments.*`
- `custom_tool_call_input.*`
- `response.output_item.{added,done}`

### `upstream.py`
- 面向单一 NewAPI 上游的 HTTP 传输客户端
- 调用上游 `/v1/chat/completions`
- 调用上游 `/v1/models`
- 处理 headers / timeout / stream

### `api/`
- HTTP 边界子模块目录
- `routes.py`：FastAPI 路由、单上游 NewAPI 透传与 request lifecycle 编排
- `policy.py`：effective-input UX guard（`empty_effective_input` / `blank_effective_input`）
- `errors.py`：统一 JSON error response 组装

### `app.py`
- 仅保留稳定门面导出，供 `uvicorn codex_chat_bridge.app:app` 与测试继续复用
- 实际 HTTP 路由与 policy 编排已下沉到 `api/` 子模块

## Phase 划分

### Phase 1：最小可运行桥（已完成）
- `GET /health`
- `GET /v1/models`
- 非流式 `/v1/responses`
- 文本型 input -> messages
- assistant text -> Responses output_text / message

### Phase 2：协议完整化（大部分已完成）
- streaming SSE ✅
- function tool calls ✅
- custom tool calls ✅
- tool_search calls ✅
- reasoning 字段 ✅
- usage / finish_reason / error 语义统一 ✅
- `function_call_output` 多轮回注 ✅
- `custom_tool_call_output` 多轮回注 ✅
- `tool_search_output` 多轮回注 ✅
- assistant tool-call history 的 `reasoning_content` 占位/回填兜底 ✅
- `system` / `developer` collapse-to-head 兼容整理 ✅
- `instructions` 文本数组拼接 / request-side `refusal` 内容拼接 / o-series `max_completion_tokens` 映射 ✅
- `refusal` / non-stream `incomplete_details` / `usage` / `created_at` ✅
- `input_image -> image_url`（顶层与 message content）✅
- `text.format -> response_format`（`json_object` 实测可用，`json_schema` 透传已接通）✅
- transform 层现已采用宽松倾向：未知顶层 item / 未知 content part 优先忽略，不再一律本地 `400 unsupported_input_item` ✅
- app 层已补 UX 守卫：`empty_effective_input` / `blank_effective_input` 本地 `400`，避免把空 messages 或纯空白 messages 交给上游炸成 `500/invalid_request` ✅
- 未完成：更完整 Responses item 家族的边角兼容

### Phase 3：生产化（已起步）
- systemd ✅
- `/opt` 持久化 ✅
- 配置文件与日志 △
- 与 NewAPI / CPA 串联验证：已完成 NewAPI；CPA 待后续链路接入

## Policy Matrix（当前冻结）

| 层 | 职责 | 当前策略 | 典型结果 |
|---|---|---|---|
| transform parity | 采用宽松的 Responses→Chat 转换语义 | 未知顶层 item、未知 content part 优先忽略；不主动为坏输入伪造新语义 | unsupported item 被丢弃，保留仍可理解的 text/image/tool 历史 |
| app UX guard | 对“已被 transform 放宽后仍不适合交给上游”的请求做本地判定 | `messages=[]` → `400 empty_effective_input`；所有 messages 都是 blank / semantically empty → `400 blank_effective_input` | 避免把明显坏请求打到上游再返回 `500/invalid_request` |
| upstream passthrough | 对已具备有效语义的请求保留上游最终裁决 | 只要仍有有效 system/user/assistant/tool/tool_calls/image 语义，就继续转发 | mixed supported+unsupported 仍正常 200；上游继续决定模型/参数级校验 |

### 当前 guard 边界
- `empty_effective_input`：归一化后没有任何 message。
- `blank_effective_input`：message 数组存在，但没有任何非空文本、图片 URL、tool_calls 或其它可判定为“有语义”的内容。
- `instructions` 若能形成非空 system message，则视为有效语义，不会被 `blank_effective_input` 拦截。
- 该矩阵的目标是：**transform 层追求 baseline parity，app 层负责本地 UX，upstream 层保留协议/模型最终判断。**

## 当前实现策略

当前骨架默认选择 **Python + FastAPI + httpx**，原因：

- 本机 Python 3.11、FastAPI、uvicorn、httpx 已可用
- 当前主机没有 `cargo` / `rustc`
- 先做一个可验证的协议桥骨架，比先恢复完整 Rust 构建链更快
- 之后如需性能/复用精确语义，再迁回 Rust 也不迟

## 风险

- 直接照搬上游项目（cc-switch）的函数不现实，需要做语言级重写/翻译
- 若只做“最小文本路径”，后续还需补 tool/reasoning/streaming
- `/v1/models` 在桥上应稳定暴露，避免再遇到网关注册问题

## 协议能力矩阵

以下矩阵区分「桥自身是否实现」和「上游 Provider 是否支持」，避免将 Provider 的能力限制误判为桥的缺陷。

| Responses Feature | Bridge | Provider-Dependent | 备注 |
|:--|:--:|:--:|:--|
| Text（input_text / output_text） | ✅ | — | 核心协议，双向完整 |
| Streaming SSE 事件 | ✅ | — | 15 种事件全覆盖（见备注 1） |
| input_image → image_url | ✅ | ✅ 上游需支持 image content part | 含 SSRF 安全校验 |
| Function Call（单/并行多 Tool） | ✅ | ✅ 上游需支持 tool_calls | 含 function/custom/tool_search 三种 kind |
| Namespace Tool | ✅ | — | flatten→restore 完整 roundtrip |
| Reasoning 全程保留（input→SSE→roundtrip） | ✅ | ✅ 上游需返回 reasoning_content | 流式/非流式均支持 |
| refusals | ✅ | — | 非流式 + content array 两种路径 |
| input_file / input_audio | ❌ | ✅ 上游需对应 content part | 桥无该映射逻辑 |
| computer_call / computer_call_output | ❌ | ✅ 上游需对应 tool type | 桥无该映射逻辑 |
| previous_response_id | ✅ | — | 内存存储，TTL 1h 惰性清理，messages + tool_context + model 持久化 |
| Hosted Tools（web_search / code_interpreter 等） | ⬜ | ✅ 上游必须原生支持 | 桥拒绝转换此类 built-in tool |
| MCP Tool | ⬜ | — | 经 custom tool 通道透传，无 MCP 协议适配 |
| usage / incomplete_details | ✅ | — | 双向映射 |
| text.format → response_format | ✅ | ✅ 上游需支持 response_format | json_object / json_schema 均已验证 |
| metadata | ⬜ | — | 透传字段，无桥层语义 |

**备注 1：SSE 事件清单**

response.created / response.in_progress / response.output_item.added / response.output_item.done / response.output_text.delta / response.output_text.done / response.content_part.added / response.content_part.done / response.reasoning_summary_text.delta / response.reasoning_summary_text.done / response.reasoning_summary_part.added / response.reasoning_summary_part.done / response.function_call_arguments.delta / response.function_call_arguments.done / response.completed / response.failed

**备注 2：previous_response_id**（已实现）

当前使用进程内 SessionStore（dict + TTL），每次响应完成后保存 messages、tool_context、model。后续请求携带 `previous_response_id` 时，从 store 恢复上下文并将新 input items 追加到已有消息列表后。单进程足够，如需多进程/持久化替换 SessionStore 后端即可。
