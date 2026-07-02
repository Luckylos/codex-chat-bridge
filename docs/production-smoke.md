# Production Smoke Checklist

_更新时间：2026-07-02_

## 1. 用途

本清单用于发布前 / 重构后 / 服务重启后，快速确认 codex-chat-bridge 的生产候选状态。

目标：

- 先验证 bridge 自身
- 再验证 top-layer alias surface
- 再验证真实 Hermes CLI
- 最后记录 blocker，而不是把 blocker 假装成通过

---

## 2. 预检

### 2.1 全量测试
```bash
cd /opt/codex-chat-bridge
.venv/bin/pytest -q
```

当前基线（2026-07-02）：
- `246 passed, 1 warning`

### 2.2 服务健康
```bash
systemctl restart codex-chat-bridge.service
curl -fsS http://127.0.0.1:18090/health
```

通过标准：
- systemd `active (running)`
- `{"ok":true,"service":"codex-chat-bridge","upstream_reachable":true}`

---

## 3. Alias visibility smoke

### 3.1 NewAPI
```bash
python3 - <<'PY'
import json, yaml, urllib.request
cfg=yaml.safe_load(open('/root/.hermes/config.yaml'))
api_key=cfg['custom_providers'][0]['api_key']
req=urllib.request.Request('http://127.0.0.1:3000/v1/models', headers={'Authorization': f'Bearer {api_key}'})
with urllib.request.urlopen(req, timeout=60) as resp:
    data=json.loads(resp.read().decode())
print('\n'.join(item.get('id','') for item in data.get('data',[]) if 'codex' in item.get('id','')))
PY
```

### 3.2 CLIProxyAPI
```bash
curl -fsS http://127.0.0.1:8317/v1/models -H 'Authorization: Bearer sk-luckyss'
```

通过标准：
- `deepseek-v4-flash-codex` 可见
- 需要验证的其它 alias 也应可见；若不可见，记为 blocker

---

## 4. Bridge / alias-surface 核心 smoke

## 4.1 deepseek 基础非流式
```bash
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请只回复 OK'
```

当前期望：
- 输出 `OK`

## 4.2 deepseek 工具调用
```bash
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请使用一个工具读取 /etc/hostname，并且最终只输出主机名，不要附加解释。'
```

当前期望：
- 输出当前主机名（本机观测值：`Hermes`）

## 4.3 deepseek continuation
建议使用脚本化 `/v1/responses` stream 验证：
- 第 1 轮：`hello`
- 第 2 轮：带 `previous_response_id` 回 `world`

通过标准：
- `response.completed`
- 第 2 轮 message text = `world`

## 4.4 nested namespace smoke
建议使用脚本化 `/v1/responses` stream 验证：
- `nested_oneof`
- `nested_anyof`

通过标准：
- `response.output_item.added` / `done` 为具体 action 名
- 不是 namespace placeholder

## 4.5 explicit namespace `tool_choice`
建议使用脚本化 `/v1/responses` stream 验证（raw bridge，`model=deepseek-v4-flash`）：
- 强制 `tool_choice={type:function,name:codex,namespace:codex}`

通过标准：
- `response.completed`
- `added_names = ["shell"]`
- `arg_deltas = ['{"command":"pwd"}']`

---

## 5. 当前 live 结果（2026-07-02）

### PASS
- deepseek alias visible
- deepseek 非流式 PASS
- deepseek CLI PASS
- deepseek 工具调用 PASS
- deepseek continuation PASS
- nested namespace PASS
- explicit namespace `tool_choice` PASS

### 非当前关注目标
- `glm-5.1-codex` 已降级为历史参考
- 原因：用户已确认其主力上游失效
- 当前 production smoke 不再把它计入通过/失败判断

---

## 6. 失败时的分层判断

### A. `/health` 失败
优先查：
- systemd
- uvicorn 启动日志
- bridge import/runtime 错误

### B. `/v1/models` 不可见
优先查：
- NewAPI channel/group
- CLIProxy alias export
- distributor routing

### C. `/v1/responses` 400/503
优先区分：
- bridge 自身 envelope / stream 问题
- explicit `tool_choice` + thinking mode 冲突
- alias/channel 不可用

### D. CLI 失败但 API 成功
优先查：
- top-layer CLI 参数
- 上层 provider/channel selection
- agent-loop 差异

---

## 7. 发布前最小必跑集

如果时间有限，至少跑这三条：

```bash
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请只回复 OK'
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请使用一个工具读取 /etc/hostname，并且最终只输出主机名，不要附加解释。'
python3 - <<'PY'
print('run explicit namespace tool_choice stream probe here')
PY
```

---

## 8. 一句话结论

> **生产 smoke 的核心不是证明“一切都成功”，而是快速证明主链路通过，并把当前 blocker 准确归类。**
