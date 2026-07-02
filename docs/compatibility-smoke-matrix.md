# Compatibility Smoke Matrix

_更新时间：2026-07-02_

## 1. 用途

本矩阵用于记录 **codex-chat-bridge 当前生产候选版本** 在真实链路上的兼容性验证结果。

目标不是替代单元测试，而是把以下三类证据沉淀为可复核的工程基线：

1. **top-layer alias surface**（NewAPI / CLIProxyAPI）
2. **raw bridge surface**（`127.0.0.1:18090`）
3. **真实 Hermes CLI / agent loop**

---

## 2. 当前 live 基线

### 2.1 代码与服务

- 分支：`main`
- Phase A 收口提交已完成
- 当前全量测试：`197 passed, 1 warning`
- 服务：`codex-chat-bridge.service` 运行中
- health：`GET /health -> {"ok":true,...}`

### 2.2 当前验证口径

本矩阵只记录 **当前时刻真实可观察状态**：

- 现在成功 = PASS
- 现在失败 = BLOCKED / FAIL
- 曾经成功但当前失败，不记为 PASS，而记为“历史成功，当前阻塞”

---

## 3. Alias 可见性矩阵

| 模型 alias | NewAPI `/v1/models` | CLIProxy `/v1/models` | 当前结论 |
|---|---:|---:|---|
| `deepseek-v4-flash-codex` | PASS | PASS | 当前可见 |
| `glm-5.1-codex` | N/A | N/A | 已降级为历史参考，不再作为当前验收目标 |
| `glm-5.2-codex` | PASS | PASS | 当前可见 |

### 3.1 当前 live 观察

实际观测到：

- NewAPI `/v1/models` 当前仅包含：
  - `deepseek-v4-flash-codex`
  - `glm-5.2-codex`
- CLIProxy `/v1/models` 当前同样仅包含：
  - `deepseek-v4-flash-codex`
  - `glm-5.2-codex`

### 3.2 关于 `glm-5.1-codex`

当前用户决策已变更：

- `glm-5.1-codex` 的主力上游已经失效
- 该 alias **不再作为当前阶段的主验收 / 阻塞项**
- 它保留为历史案例：说明 alias-surface / channel-availability 问题如何与 bridge 协议问题区分

因此，在本矩阵里它当前记为：

- **历史参考（非当前验收目标）**

如果未来用户重新要求恢复这条 alias，再单独启动 channel / distributor / alias export 排查。

---

## 4. 协议能力 smoke 矩阵（当前 live）

### 4.1 `deepseek-v4-flash-codex`

| 场景 | 表面 | 结果 | 说明 |
|---|---|---|---|
| `/v1/responses` 非流式 | NewAPI alias | PASS | 返回 `status=completed`，消息 `OK` |
| `/v1/responses` 流式 | NewAPI alias | PASS | SSE 完整结束 |
| `previous_response_id` continuation | NewAPI alias | PASS | `hello -> world` 已通过 |
| Hermes CLI 基础问答 | top-layer CLI | PASS | `请只回复 OK` -> `OK` |
| Hermes CLI 工具调用 | top-layer CLI | PASS | 成功读取 `/etc/hostname` |
| nested namespace `nested_oneof` | NewAPI alias stream | PASS | `added_names/done_names = shell` |
| nested namespace `nested_anyof` | NewAPI alias stream | PASS | `added_names/done_names = read_file` |
| explicit namespace `tool_choice` | NewAPI alias stream | PASS | thinking-mode compat retry 后通过 |
| explicit namespace `tool_choice` | raw bridge stream | PASS | `shell` + `{"command":"pwd"}` |

### 4.2 `glm-5.1-codex`（历史参考）

| 场景 | 表面 | 当前状态 | 说明 |
|---|---|---|---|
| alias 可见性 / 请求可用性 | NewAPI / CLIProxy / CLI | 不纳入当前验收 | 主力上游已失效，当前用户明确要求不再追踪 |
| 历史价值 | 故障分类样例 | 保留 | 用于说明 alias/channel blocker 与 bridge 协议回归的区别 |

### 4.3 `glm-5.2-codex`

| 场景 | 表面 | 结果 | 说明 |
|---|---|---|---|
| `/v1/models` 可见性 | NewAPI / CLIProxy | PASS | 当前可见 |
| 其它 smoke | 未执行 | PENDING | 本轮不是主验收模型 |

---

## 5. 关键 live 证据摘要

### 5.1 deepseek continuation

已验证：

- 第 1 轮流式：`hello`
- 第 2 轮 `previous_response_id`：`world`

说明：

- stream finalize 正常
- replay / assistant persistence 正常
- `msg_<response_id>` continuity 不阻断 continuation

### 5.2 explicit namespace `tool_choice`

当前 live 结果：

```json
{
  "terminal_event": "response.completed",
  "added_names": ["shell"],
  "done_names": ["shell"],
  "arg_deltas": ["{\"command\":\"pwd\"}"]
}
```

说明：

- forced namespace tool path 已可用
- shared nested namespace normalizer 已命中
- thinking-mode compat retry 已命中并生效

### 5.3 glm alias channel blocker

当前 live 错误：

```text
No available channel for model glm-5.1-codex under group default
```

说明：

- 问题在 top-layer alias / distributor / channel availability
- 不是 Phase A 中 bridge 结构提交造成的协议层失败

---

## 6. 当前判断

### PASS
- `deepseek-v4-flash-codex` 作为当前主验收模型，协议主链路已通过
- nested namespace / continuation / explicit tool_choice 已进入 live verified 状态

### 非当前关注目标
- `glm-5.1-codex` 不再作为当前 Phase B 验收或 blocker
- 原因：用户已确认其主力上游失效，当前无需继续追踪恢复

---

## 7. 建议的 Phase B 后续动作

1. 继续把 `deepseek-v4-flash-codex` 作为 bridge 协议主验收 canary
2. 把 `glm-5.1-codex` 作为历史故障案例保留，不再纳入当前 smoke 通过/失败判断
3. 若未来重新纳入验收，再优先检查：
   - NewAPI channel/group/distributor
   - CLIProxy alias export
   - 对应 channel 的 models 可见性

---

## 8. 一句话结论

> **当前 live smoke 证明：bridge 协议主链路以 `deepseek-v4-flash-codex` 为 canary 已通过；`glm-5.1-codex` 已降级为历史参考，不再作为当前验收目标。**
