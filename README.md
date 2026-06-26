# codex-chat-bridge

最小独立协议桥：**Responses-speaking client -> Chat Completions upstream**。

纯透明协议桥：**Responses-speaking client -> Chat Completions upstream**。

不再具备多上游路由、provider 管理、TUI 等 cc-switch 附带功能。

## 当前模块图

```text
codex_chat_bridge/
├── app.py                      # 稳定 FastAPI 门面，兼容 uvicorn/systemd 导入路径
├── api/                        # HTTP 边界：routes / policy / JSON errors
├── bridge_context/             # request-scoped tool context 单一真相源
├── responses_to_chat/          # Responses -> Chat 请求转换
├── chat_to_responses/          # Chat JSON -> Responses 非流式恢复
├── stream_chat_to_responses.py # SSE 入口与块分发门面
├── stream_responses_state.py   # 流式状态机门面
├── stream_state/               # 流式 envelope / message / tools 域模块
├── response_semantics.py       # 共享协议语义小工具
├── upstream.py                 # 单上游 Chat Completions / models 传输客户端
└── models.py                   # 最小请求/响应数据模型
```

### 扩展落点
- 新增 request-side tool / item family：优先落在 `bridge_context/` + `responses_to_chat/`
- 新增非流式响应 item 恢复：优先落在 `chat_to_responses/`
- 新增流式事件兼容：优先落在 `stream_state/`
- 新增本地 UX 守卫 / 错误边界：优先落在 `api/policy.py` 与 `api/errors.py`

## 当前已完成

### 服务面
- `GET /health`
- `GET /v1/models`（透传单一 NewAPI）
- `POST /v1/responses`
- `POST /v1/responses/compact`

### 请求转换：Responses -> Chat Completions
- `model` 必须由下游显式提供；bridge 不再使用本地默认模型回填
- bridge 上游仅对接单一 NewAPI 入口，不再内建多上游路由；NewAPI 自身负责模型聚合与分发
- `instructions -> system`（支持字符串与文本数组拼接）
- 文本 `input` / 顶层 `input_text` item / 通用 `message`
- 顶层 `input_image` / message content 内 `input_image` -> Chat `image_url` content parts
- request-side `refusal` content parts 会按文本参与 Chat content 组装
- `text.format -> response_format`（已验证 `json_object`；兼容 `json_schema` 透传）
- 顶层 `response_format` 兜底透传到 Chat upstream
- `reasoning.effort` 透传；`off/disabled -> none`
- `thinking enabled/disabled`
- function tools / `tool_choice`
- custom tools / custom `tool_choice`
- tool_search / tool_search `tool_choice`
- `function_call -> assistant.tool_calls`
- `custom_tool_call -> assistant.tool_calls`
- `tool_search_call -> assistant.tool_calls`
- assistant `tool_calls` 历史缺少 `reasoning_content` 时会自动补最小占位；尾随 `reasoning` item 会回填到前一个 assistant tool-call message
- `function_call_output -> role=tool + tool_call_id`
- `custom_tool_call_output -> role=tool + tool_call_id`
- `tool_search_output -> role=tool + tool_call_id`
- request-scoped tool context 已独立沉到 `bridge_context/`，统一管理 custom tools / tool_search / namespace tools 与 `tool_search_output` 动态注入
- 通用 message 形态下保留：
  - `assistant.tool_calls`
  - `assistant.reasoning_content`
  - `tool.tool_call_id`
- `system` / `developer` 历史消息会合并到头部单一 system message，兼容上游 Chat 语义
- 架构上已拆为 `responses_to_chat/common.py` + `items.py` + `request.py`，原 `transform_responses_to_chat.py` 仅作稳定门面

### 响应转换：Chat Completions -> Responses
- 上游真实返回的 `model` 会原样回写到 Responses body
- assistant text -> `message` + `output_text`
- assistant `tool_calls` -> `function_call` / `custom_tool_call` / `tool_search_call`
- `reasoning_content` / `reasoning` -> `reasoning`
- `refusal` / refusal content parts -> `message.content[].type=refusal`
- `finish_reason=length` -> `status=incomplete` + `incomplete_details.reason=max_output_tokens`
- `usage` / `created_at` 已恢复到非流式 Responses body
- `tool_search_output` 中携带的 namespace tools 会进入当前 request-scope tool context
- 架构上已拆为 `chat_to_responses/common.py` + `tools.py` + `response.py`，原 `transform_chat_to_responses.py` 仅作稳定门面

### 流式：Chat SSE -> Responses SSE
- `response.created`
- `response.in_progress`
- `response.output_item.added`
- `response.content_part.added`
- `response.output_text.delta`
- `response.output_text.done`
- `response.content_part.done`
- `response.function_call_arguments.delta`
- `response.function_call_arguments.done`
- `response.custom_tool_call_input.delta`
- `response.custom_tool_call_input.done`
- `response.output_item.done`
- `response.reasoning_summary_*`
- `response.completed`
- `response.failed`
- stream truncation / `finish_reason=length` 会正确落到 `incomplete_details`
- `response.failed` 现在会保留已完成的部分 output（如已完成的 reasoning item）
- 已支持 `delta.content` 为数组时恢复 `output_text` / `refusal` content parts

## 明确不做的语义
- 不再做 `max -> xhigh` 兼容映射
- 不负责 provider DB / failover / 本地 CLI 管理
- 不实现与目标桥无关的 Claude / Gemini / 桌面态能力
- transform 层采用宽松语义：未知/不支持 item 可被忽略，而不是一律本地拒绝
- app 层本地 UX guard 会在归一化后拦截：
  - `400 empty_effective_input`
  - `400 blank_effective_input`
- 当前配置上游实测：HTTPS `image_url` 可通；`data:` URL 会被上游 `400 invalid_request_error` 拒绝（桥本地转换已支持，但上游不接受）

## 仍未完成
- 更细的 Responses item 家族兼容仍可继续补（当前已覆盖高价值 text/refusal/reasoning/tool path）
- 更强的多轮会话状态兼容层（当前以最小 function/custom/tool_search path 为主）

## 运行方式

### 开发态
```bash
cd /opt/codex-chat-bridge
.venv/bin/uvicorn codex_chat_bridge.app:app --host 127.0.0.1 --port 18090
```

### 当前宿主机正式服务
- systemd: `codex-chat-bridge.service`
- 监听：`0.0.0.0:18090`

## 环境变量
- `BRIDGE_UPSTREAM_BASE_URL`
  - NewAPI 的单一入口；bridge 只连这一层
- `BRIDGE_UPSTREAM_API_KEY`
- `BRIDGE_UPSTREAM_TIMEOUT_SECONDS`
- `BRIDGE_PUBLIC_BASE_URL`

## 最小回归
```bash
cd /opt/codex-chat-bridge
.venv/bin/python -m unittest -v tests/test_multiturn_tool_roundtrip.py
```

详见 `ARCHITECTURE.md`。
