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

| 变量 | 说明 |
|:--|:--|
| `BRIDGE_UPSTREAM_BASE_URL` | NewAPI 入口 |
| `BRIDGE_UPSTREAM_API_KEY` | API 密钥 |
| `BRIDGE_UPSTREAM_TIMEOUT_SECONDS` | 上游超时（默认 60） |
| `BRIDGE_PUBLIC_BASE_URL` | 对外暴露地址 |

## 暴露的端点

- `GET  /health`
- `GET  /v1/models`
- `POST /v1/responses`
- `POST /v1/responses/compact`

## 功能边界

- 非流式 + 流式 Responses → Chat 双向转换
- 文本 / 图片 / refusal / reasoning / function-call / custom-tool / tool-search 已覆盖
- 不做多上游路由、provider 管理、本地 CLI
- 详见 [`ARCHITECTURE.md`](ARCHITECTURE.md)