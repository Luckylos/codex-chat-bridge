# Alias-Surface Validation

_更新时间：2026-07-02_

## 1. 原则

对 codex-chat-bridge 这类 **single-upstream protocol bridge**，模型可用性验证必须遵循：

> **先验证 top-layer alias surface，再下钻 raw bridge / raw upstream 解释原因。**

原因：

- 用户实际用的是 alias（例如 `deepseek-v4-flash-codex` / `glm-5.1-codex`）
- alias 的存在、导出、channel 分配、group/distributor 可用性，都不由 raw bridge 单独决定
- raw bridge 只能证明协议转换能力，不能单独证明 alias 在真实入口可用

---

## 2. 验证顺序（冻结）

### Step 1 — Top-layer visibility
先查：

- `NewAPI /v1/models`
- `CLIProxyAPI /v1/models`

目的：
- 确认 alias 名是否真的被当前入口暴露

### Step 2 — Top-layer request
再测：

- `POST /v1/responses`
- `hermes chat --provider custom:newapi -m <alias>`

目的：
- 确认 alias 不仅“能看到”，还“能真正用”

### Step 3 — Raw bridge probe
只有在 top-layer 失败后，再查：

- raw bridge `/v1/responses`
- raw upstream model target

目的：
- 判定失败是：
  - alias/export 问题
  - distributor/channel 问题
  - raw upstream target liveness 问题
  - bridge protocol 问题

---

## 3. 当前案例

## 3.1 `deepseek-v4-flash-codex`

当前 live 状态：

- `/v1/models` 可见
- `/v1/responses` 可用
- Hermes CLI 可用
- tool loop 可用
- nested namespace / explicit tool_choice 可用

结论：
- **当前 bridge 主协议 canary = PASS**

## 3.2 `glm-5.1-codex`

### 历史事实
- 历史上它曾被恢复并通过过 top-layer 验证
- 当时根因修复点在 alias target liveness，而非 bridge 协议修复

### 当前 live 状态
- `/v1/models` 不再列出它
- CLI 失败：
  - `No available channel for model glm-5.1-codex under group default`
- NewAPI 日志同样显示：
  - `No available channel for model glm-5.1-codex under group default (distributor)`

### 结论
这说明当前失败层级是：

- **top-layer alias/channel availability**

而不是：

- raw bridge 协议回归

因此当前不能把 `glm-5.1-codex` 记为 PASS。

---

## 4. 工程判断规则

### PASS 的条件
一个 alias 只有同时满足以下条件，才可记为 PASS：

1. `/v1/models` 当前可见
2. `POST /v1/responses` 当前成功
3. Hermes CLI（如适用）当前成功

### BLOCKED 的条件
出现以下任一情况，应记为 BLOCKED：

1. `/v1/models` 当前不可见
2. distributor / channel unavailable
3. CLI / top-layer surface 返回 model_not_found / no available channel

### FAIL（bridge/protocol）的条件
只有在：

- alias 可见
- channel 正常
- raw upstream 可用
- 但 bridge 返回错误 envelope / stream 错误 / wrong item graph

才应把问题归类为 bridge/protocol FAIL。

---

## 5. 当前结论（2026-07-02）

- `deepseek-v4-flash-codex`：**PASS**
- `glm-5.1-codex`：**BLOCKED（alias/channel availability）**

这一区分非常重要，因为它决定后续动作完全不同：

- PASS / FAIL -> 继续修 bridge
- BLOCKED -> 应先查 alias export / channel / distributor

---

## 6. 一句话原则

> **不要用 raw bridge 的成功或失败，替代 top-layer alias surface 的真实可用性结论。**
