# codex-chat-bridge

透明协议桥：**Responses-speaking client → Chat Completions upstream**。

设计为对接单一 NewAPI 上游，由 NewAPI 负责 provider 聚合与模型路由，bridge 只做协议转换。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/uvicorn codex_chat_bridge.app:app --host 127.0.0.1 --port 18090
```

## 环境变量

| 变量 | 说明 | 默认值 |
|:--|:--|:--|
| `BRIDGE_UPSTREAM_BASE_URL` | NewAPI 入口（必填） | — |
| `BRIDGE_UPSTREAM_API_KEY` | API 密钥 | 空 |
| `BRIDGE_UPSTREAM_TIMEOUT_SECONDS` | 上游超时 | `60` |
| `BRIDGE_UPSTREAM_STREAMING` | 是否以流式方式请求上游 | `true` |
| `BRIDGE_UPSTREAM_MAX_RETRIES` | 400 兼容回退最大重试次数 | `2` |
| `BRIDGE_MAX_CONCURRENT_REQUESTS` | 最大并发请求数 | `20` |
| `BRIDGE_UNSUPPORTED_TOOL_POLICY` | 无法映射的 Responses 内置工具策略（`ignore` / `reject` / `passthrough`） | `ignore` |

## 暴露的端点

- `GET  /health`
- `GET  /metrics`（Prometheus）
- `GET  /v1/models`
- `POST /v1/responses`
- `POST /v1/responses/compact`

## 架构概览

```
Responses client → /v1/responses → routes.py → response_service.py → responses_to_chat/ → upstream →
                                                                            ↑                      ↓
                                                                 chat_to_responses/ ← Chat Completions response
                                                                            ↓
                                                                 Responses SSE/JSON → client
```

核心模块：
- **`api/routes.py`** — 薄 HTTP 层：路由、并发门控、FastAPI 入口
- **`api/response_service.py`** — 响应服务层：请求编排、会话解析、上游错误归一化、流式/非流式分发
- **`responses_to_chat/`** — Responses→Chat 请求转换（单职拆分：items/content/media/tools/request/orphan/errors）
- **`chat_to_responses/`** — Chat→Responses 响应恢复（对称 convert() 入口：response/text/tools/annotations/inline_think）
- **`protocol/`** — SSE 解析、会话存储、TypedDict 类型定义
- **`stream_state/`** — 流式状态机（envelope/message/reasoning/tools）
- **`inline_think_sm.py`** — InlineThink 三态状态机（detecting→reasoning→text），独立于 MessageState
- **`errors.py`** — BridgeError 异常层级，统一错误传播
- **`bridge_context/`** — 请求级工具上下文（schema 注册、namespace 映射、nested namespace normalizer）
- **`response_semantics.py`** — 共享响应语义 + REQUEST_ECHO_FIELDS 唯一定义
- **`tool_arguments.py`** — canonicalize_tool_arguments（JSON 排序/归一化）
- **`reasoning_policy.py`** — canonical effort 归一化 + provider bucket 分发
- **`upstream_compat.py`** — 400 compat retry（含 explicit `tool_choice` / thinking-mode 冲突回退）

详见 [`ARCHITECTURE.md`](ARCHITECTURE.md)

## 当前 reasoning 策略

bridge 将调用方的 reasoning 强度归一化为四档 canonical effort：

- `unspecified`（未传）
- `none`（`off`/`disabled`/`false`/`minimal`/`none`）
- `high`（`low`/`medium`/`high`）
- `xhigh`（`max`/`xhigh`）

内部按 provider 能力分为两类：

| 内部类别 | 行为 | 适用模型 |
|:--|:--|:--|
| `effort` | `unspecified` → `provider_default`；有显式 effort → 仅传 `reasoning_effort` | deepseek、glm、openai-like 等 |
| `passthrough` | 始终 `provider_default`，不传 reasoning 参数 | kimi 等 |

> 旧的 4 bucket（openai_like / deepseek / glm / kimi）已合并为上述 2 类。

更完整的冻结设计见：[`docs/reasoning-policy-freeze.md`](docs/reasoning-policy-freeze.md)

## 功能边界

- 非流式 + 流式 Responses ↔ Chat 双向转换
- 文本 / 图片 / `input_audio` / refusal / reasoning / function-call / custom-tool / tool-search 已覆盖
- `previous_response_id` 会话延续（messages 深拷贝隔离 + tool_context 合并 + TTL 自动续期；streamed assistant replay 保留 chat-side tool shape）
- Hosted Responses tools 行为可配置：`ignore` / `reject` / `passthrough`
- 不做多上游路由、provider 管理、本地 CLI

## 当前生产 smoke canary

当前 Phase B 的主验收 canary：

- `deepseek-v4-flash-codex`

说明：

- `glm-5.1-codex` 已降级为历史参考，因为其主力上游已失效；当前不再作为主验收目标。
- 兼容矩阵与 alias-surface 验证见：
  - [`docs/compatibility-smoke-matrix.md`](docs/compatibility-smoke-matrix.md)
  - [`docs/alias-surface-validation.md`](docs/alias-surface-validation.md)
  - [`docs/production-smoke.md`](docs/production-smoke.md)

## 测试

```bash
.venv/bin/python -m pytest tests/ -v    # 当前基线：246 passed, 1 warning
```
