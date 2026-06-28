# Reasoning Policy Freeze

_Last updated: 2026-06-28_

## 目标

冻结 codex-chat-bridge 下一轮 **reasoning / thinking 大重构** 的目标方向，作为后续实现、测试与代码评审的共同基线。

本文件只定义：

1. **调用方语义如何收敛**
2. **不同 provider bucket 的最终 HTTP 请求体策略**
3. **哪些旧逻辑要退休/下沉**
4. **这次重构明确不做什么**

---

## 1. 当前长期边界

bridge 仍然是：

```text
Responses-speaking client -> codex-chat-bridge -> Chat Completions upstream (single NewAPI)
```

职责保持为：

- Responses ↔ Chat Completions 协议转换
- reasoning 语义提取
- upstream 参数编码
- 兼容回退（400 compatibility fallback）
- stream / non-stream 一致性

**不扩展为：**

- 多 upstream router
- provider capability center
- model catalog / provider business semantic hub
- 任意 unknown-field passthrough / extra_body diffusion layer

---

## 2. 为什么要做这次大重构

当前 reasoning 相关逻辑已经出现“双编码 / 双真相源”问题：

- `responses_to_chat/request.py` 先构造一版 `thinking` / `reasoning_effort`
- `reasoning_policy.py` 在 upstream 发送前再重写一版

这带来几个问题：

1. **阅读成本高**：中间体不等于最终发往 upstream 的真实请求体。
2. **审计困难**：调用方传入的 effort 是否真的下发，不够一眼清楚。
3. **策略漂移**：`config.py` / `request.py` 的 legacy `ReasoningMode` 语义与当前真实 provider policy 已不再完全一致。
4. **扩展困难**：继续小补丁只会让 reasoning 逻辑更分散。

因此，本轮重构不是继续补丁，而是要把 reasoning 收敛成 **单一真相源 + provider encoder**。

---

## 3. 冻结后的核心原则

### 3.1 单一真相源

后续重构完成后，**最终 upstream reasoning 请求体的唯一真相源** 应为：

- `reasoning_policy.py`（或其重构后的同层模块）
- `upstream.py` 的统一发送层

`responses_to_chat/request.py` 只负责：

- 从 Responses API 请求里提取基础 Chat body
- 抽取 calling intent（含 reasoning intent）
- **不再直接决定最终 provider-specific wire dialect**

---

### 3.2 显式强度只保留三档

内部 canonical reasoning effort 只保留四种状态：

- `unspecified`
- `none`
- `high`
- `xhigh`

其中：

- `unspecified` = 调用方没有显式传 effort
- 其余三项 = 调用方明确表达的推理强度意图

#### 输入归一化规则

| 调用方输入 | bridge 内部 canonical 值 |
|---|---|
| 未传 | `unspecified` |
| `off` / `disabled` / `false` | `none` |
| `none` / `minimal` | `none` |
| `low` / `medium` | `high` |
| `high` | `high` |
| `xhigh` / `max` | `xhigh` |

说明：

- bridge 内部不再保留 `low` / `medium` / `max` 作为独立长驻状态
- `xhigh` 视为“最高显式档”
- 若某上游 / 网关会把 `xhigh` 继续映射到 `max`，由上游处理，bridge 不额外承担该 provider-specific 二次翻译责任

---

### 3.3 未指定时保留 provider default

对于没有显式 effort 的调用：

- bridge **不主动伪造强度**
- 保留各 provider 自己的默认策略

这是为了兼容：

- SillyTavern / 普通网页聊天
- 未提供 reasoning knob 的上层调用方
- provider 自带的默认动态思考/默认深推策略

---

### 3.4 显式 effort 时尽量保留调用方意图

对于 Hermes / agent 工具等显式传了 effort 的调用：

- bridge 不应静默吃掉强度意图
- 应尽量按 provider bucket 转义并显式下发
- 真正不支持该档位/字段时，再通过 compatibility fallback 优雅降级

---

## 4. Provider bucket 冻结

后续 reasoning provider policy 冻结为四类：

1. `openai_like`
2. `deepseek`
3. `glm`
4. `kimi`

注意：

- 这里的分类是 **reasoning-encoding bucket**，不是全局 provider 能力中心
- bridge 只在 reasoning 参数编码层区分这些 bucket
- bucket 选择允许依赖 **模型名规则匹配**，这是当前架构下最简单且最可控的策略

---

## 5. Bucket 级编码规则（冻结）

## 5.1 `openai_like`

适用：

- 标准 OpenAI / GPT 类上游
- 只提供 Chat Completions 格式，但 reasoning 参数更接近标准 OpenAI 语义的厂商

### `unspecified`

发送：

- `provider_default`
- 即：不传 `thinking`，不传 `reasoning_effort`

### `none` / `high` / `xhigh`

发送：

```json
{
  "reasoning_effort": "<none|high|xhigh>"
}
```

说明：

- `openai_like` 不应默认再混入 `thinking={"type":"enabled"}`
- bridge 继续负责 `Responses -> Chat request -> Chat response -> Responses` 的协议转换，但在 reasoning 控制上采用 **effort-first** 风格

---

## 5.2 `deepseek`

### `unspecified`

发送：

- `provider_default`

说明：

- DeepSeek 官方文档表明 thinking 默认开启，常规请求默认 effort=`high`
- 因此未指定时不需要 bridge 额外伪造强度

### `none` / `high` / `xhigh`

发送：

```json
{
  "reasoning_effort": "<none|high|xhigh>"
}
```

说明：

- DeepSeek 归到 **effort-first**
- 不要求 bridge 默认额外传 `thinking={"type":"enabled"}`
- 如果实际链路中 `thinking` 也是可接受字段，那属于兼容 fallback / provider 容忍范围，不是首发策略

---

## 5.3 `glm`

### `unspecified`

发送：

- `provider_default`

说明：

- GLM 官方文档表明 `thinking.type` 默认 `enabled`
- `GLM-5.2+` 的 `reasoning_effort` 默认且推荐 `max`
- 因此未指定时，bridge 不应先抢着注入 `thinking` / `reasoning_effort`

### `none`

首发发送：

```json
{
  "thinking": {"type": "disabled"}
}
```

必要时（未来如验证需要）可评估是否补充：

```json
"reasoning_effort": "none"
```

但冻结基线以 `thinking.disabled` 为准。

### `high` / `xhigh`

首发发送：

```json
{
  "thinking": {"type": "enabled"},
  "reasoning_effort": "<high|xhigh>"
}
```

说明：

- GLM 归到 **thinking + effort** 方言
- 这里的 `reasoning_effort` 是否最终被上游/网关映射为 `max` 等内部值，由上游负责
- bridge 不再自己保留 `max` 内部态

---

## 5.4 `kimi`

### 全部情况

发送：

- `provider_default`

即：

- 不主动传 `thinking`
- 不主动传 `reasoning_effort`

说明：

- `kimi-k2.6`：thinking 默认开启
- `kimi-k2.7-code`：官方明确不需要/不应该传 `thinking`
- 当前公开接口不是 effort-knob-first 模型，因此 bridge 不尝试伪造 `none/high/xhigh` 的精确 provider 映射

---

## 6. 模型名规则匹配（冻结方向）

当前架构下，provider bucket 选择允许使用**模型名规则匹配**。

原则：

1. 规则表集中维护
2. 先具体、后宽泛
3. 匹配结果只用于 **reasoning bucket** 选择，不扩展成全局 provider router

建议方向（示例）：

```python
MODEL_REASONING_BUCKET_RULES = [
    (r"^deepseek", "deepseek"),
    (r"^(glm|zhipu|bigmodel)", "glm"),
    (r"^(kimi|moonshot)", "kimi"),
    (r".*", "openai_like"),
]
```

说明：

- 这里的兜底不表示“所有未知 provider 都真的是 OpenAI”，只表示在 reasoning 编码上先采用 `openai_like` 的 `reasoning_effort` 策略
- 400 compatibility fallback 仍负责兜底

---

## 7. Compatibility fallback（保持但收敛）

大重构后，仍保留统一的 stream / non-stream 400 compatibility fallback。

### reasoning 相关 fallback 的原则

- 首发先按 bucket 编码
- 若上游返回 400 incompatible reasoning fields
- 根据错误文本 + 当前编码做方向性降级
- `none` 始终是最后兜底

### 方向性原则

- `openai_like` / `deepseek`
  - effort 被拒 -> 回退到 `provider_default` 或 `none`
- `glm`
  - `thinking` 被拒 -> 回退到 `provider_default` / `none`
  - `reasoning_effort` 被拒 -> 尝试仅保留 `thinking`
- `kimi`
  - 原则上首发即 `provider_default`，不依赖 reasoning fallback

非 reasoning 的 400 compat 规则继续保留：

- `top_p`
- `stream_options`
- `include_usage`
- `parallel_tool_calls`

并继续要求：

- **stream / non-stream 共用一套兼容框架**

---

## 8. 旧逻辑的去留（冻结）

## 8.1 `responses_to_chat/request.py`

后续应收缩为：

- 解析 Responses 请求
- 抽取 reasoning semantic intent
- 构造基础 Chat body

后续不应继续承担：

- provider-specific reasoning wire encoding
- 深度依赖 `ReasoningMode` 的 legacy 方言分发

---

## 8.2 `config.py` 里的 `ReasoningMode`

当前 `ReasoningMode` 是历史兼容层，不再适合作为未来 reasoning 主架构的最终语义入口。

后续方向：

- 降级为 legacy compatibility config
- 或逐步退休
- 新主语义改由 canonical effort + provider bucket policy 驱动

---

## 8.3 `reasoning_policy.py`

后续应成为：

- canonical effort 归一化中心
- model-name → provider bucket 选择中心
- provider encoder 中心
- reasoning fallback step 生成中心

---

## 9. 本次大重构明确不做什么

1. 不把 bridge 扩成多 upstream router
2. 不做 provider capability knowledge base
3. 不做任意 unknown-field passthrough
4. 不为所有 provider 发明统一 `thinking` 结构
5. 不引入大范围 request-level complex config surface
6. 不为了兼容 SDK 写法而把 HTTP 协议层伪装成 `extra_body` 架构

---

## 10. 目标测试矩阵（冻结）

后续重构至少要覆盖：

### 10.1 canonical effort 归一化
- 未传 -> `unspecified`
- `off/disabled` -> `none`
- `low/medium` -> `high`
- `max/xhigh` -> `xhigh`

### 10.2 provider bucket 选择
- `deepseek-*` -> `deepseek`
- `glm-*` / `zhipu-*` / `bigmodel-*` -> `glm`
- `kimi-*` / `moonshot-*` -> `kimi`
- 其他 -> `openai_like`

### 10.3 provider encoder
- `openai_like` + `high` -> `reasoning_effort=high`
- `deepseek` + `xhigh` -> `reasoning_effort=xhigh`
- `glm` + `high` -> `thinking.enabled + reasoning_effort=high`
- `glm` + `none` -> `thinking.disabled`
- `kimi` + 任意 effort -> `provider_default`

### 10.4 compatibility fallback
- stream / non-stream 一致
- `thinking` 被拒的 glm 兼容回退
- `reasoning_effort` 被拒的 effort-first 兼容回退
- 非 reasoning compat 规则不回归

---

## 11. 重构验收标准（冻结）

当以下条件全部满足时，可认为这次大重构达标：

- [ ] reasoning 最终编码只有一个真相源
- [ ] `responses_to_chat/request.py` 不再直接承担 provider-specific reasoning wire dialect
- [ ] canonical effort 只保留 `unspecified/none/high/xhigh`
- [ ] OpenAI-like / DeepSeek / GLM / Kimi 四类 bucket 行为与本文件一致
- [ ] stream / non-stream compatibility fallback 继续统一
- [ ] 全量测试通过
- [ ] 文档 / 注释 / 测试名称与最终真实行为一致，不再出现“预构造体”和“真实 upstream 请求体”语义混淆

---

## 12. 一句话冻结

**后续大重构的目标不是继续补 reasoning 小补丁，而是把 reasoning 收敛成：`canonical effort (unspecified/none/high/xhigh)` + `model-name-selected provider bucket` + `single-source upstream encoder`，其中 OpenAI-like 与 DeepSeek 走 `reasoning_effort` 线，GLM 走 `thinking + reasoning_effort` 线，Kimi 始终保留 provider default。**
