# codex-chat-bridge

透明协议桥：**Responses-speaking client → Chat Completions upstream**。

设计为对接单一 NewAPI 上游，由 NewAPI 负责 provider 聚合与模型路由，bridge 只做协议转换。

## 为什么用

如果你的客户端只支持 Responses API（如 Codex CLI），而上游只暴露 Chat Completions 端点，这个 bridge 放在中间做双向映射，无需修改两端。

## 快速开始

```bash
pip install -r requirements.txt
uvicorn codex_chat_bridge.app:app --host 127.0.0.1 --port 18090
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
| `BRIDGE_UNSUPPORTED_TOOL_POLICY` | 无法映射的 Responses 内置工具策略 | `ignore` |
| `BRIDGE_PUBLIC_BASE_URL` | 对外暴露地址 | `http://127.0.0.1:18090/v1` |

## 当前 reasoning 策略

bridge 将调用方的 reasoning 强度归一化为四档 canonical effort：

- `unspecified`（未传）
- `none`（`off`/`disabled`/`false`/`minimal`/`none`）
- `high`（`low`/`medium`/`high`）
- `xhigh`（`max`/`xhigh`）

内部按 provider 能力分为两类：

| 内部类别 | 行为 | 适用模型 |
|:--|:--|:--|
| `effort` | `unspecified` → `provider_default`（不传参数）；有显式 effort → 仅传 `reasoning_effort` | deepseek、glm、openai-like 等 |
| `passthrough` | 始终 `provider_default`，不传 reasoning 参数 | kimi 等 |

> 旧的 4 bucket（openai_like / deepseek / glm / kimi）已合并为上述 2 类。
> `select_reasoning_provider_bucket()` 保留为外部兼容 API 但不再承载核心分发。

更完整的冻结设计见：[`docs/reasoning-policy-freeze.md`](docs/reasoning-policy-freeze.md)

## 暴露的端点

- `GET  /health`
- `GET  /metrics`（Prometheus）
- `GET  /v1/models`
- `POST /v1/responses`
- `POST /v1/responses/compact`

## 功能边界

- 非流式 + 流式 Responses → Chat 双向转换
- 文本 / 图片 / refusal / reasoning / function-call / custom-tool / tool-search 已覆盖
- `previous_response_id` 会话延续（messages 深拷贝隔离 + tool_context 合并）
- 不做多上游路由、provider 管理、本地 CLI
- 详见 [`ARCHITECTURE.md`](ARCHITECTURE.md)
