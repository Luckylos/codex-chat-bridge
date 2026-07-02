# codex-chat-bridge 生产交付级全项目重构计划

_更新时间：2026-07-02_

## 1. 目标与交付标准

本计划面向 **codex-chat-bridge 的全项目重构与生产交付**，目标不是继续做零散补丁，而是在当前“主链路协议能力已基本闭环”的基础上，把项目推进到：

- **Verifiable**：关键能力有真实 CLI / API / SSE / tool-loop 验证
- **Reversible**：每个阶段都有明确回滚边界
- **Auditable**：代码、测试、文档、发布步骤都有留痕
- **Maintainable**：结构职责清晰，避免继续累积协议兼容补丁债
- **Production-deliverable**：不仅“能用”，还包含文档、回归矩阵、发布与验收闭环

### 最终验收标准

完成后，项目应满足：

1. **协议主链路稳定**
   - `/v1/responses` 非流式、流式、`previous_response_id`、tool roundtrip、namespace tools、explicit tool_choice 全通过
2. **结构层收敛**
   - 请求构建、upstream compat、stream state、session persistence、response restoration 边界清晰
3. **测试层闭环**
   - 单元测试 + 定向回归测试 + 至少 3 组真实 alias-surface smoke 全通过
4. **文档层同步**
   - README / ARCHITECTURE / freeze docs / rollout plan / compatibility matrix 一致
5. **发布层可交付**
   - 有明确的提交切分、回滚方式、systemd 验证、上线后 smoke checklist

---

## 2. 当前基线（live verified）

### 2.1 当前代码与工作树状态

当前分支：`main`

当前未提交工作树（本轮已验证通过的在研变更）：

- `codex_chat_bridge/bridge_context/__init__.py`
- `codex_chat_bridge/bridge_context/models.py`
- `codex_chat_bridge/bridge_context/nested_namespace.py` _(new)_
- `codex_chat_bridge/chat_to_responses/tools.py`
- `codex_chat_bridge/stream_responses_state.py`
- `codex_chat_bridge/stream_state/envelope.py`
- `codex_chat_bridge/stream_state/tool_items.py`
- `codex_chat_bridge/stream_state/tools.py`
- `codex_chat_bridge/upstream_compat.py`
- `tests/test_nested_namespace_tools.py`
- `tests/test_phase2_regression.py`
- `tests/test_upstream_compat.py` _(new)_

### 2.2 当前已完成的 live 验证事实

当前版本已经通过的高价值验证：

- `deepseek-v4-flash-codex` alias-surface `/v1/responses` 非流式
- `deepseek-v4-flash-codex` 流式 + `previous_response_id`
- `deepseek-v4-flash-codex` 真实 Hermes CLI 验证
- `deepseek-v4-flash-codex` 真实工具调用验证
- nested namespace `nested_oneof` / `nested_anyof` 的真实流式 action 解析验证
- explicit namespace `tool_choice` 路径已通过 compat retry 修复并验证
- `glm-5.1-codex` 转换层当前已恢复可用（根因是 alias target liveness，而非 bridge 重构本身）

### 2.3 当前工作树分组（Phase A 实施边界）

为避免把结构收敛、回放修复、compat 兼容混成一个不可审计的大提交，当前工作树应按以下 3 组收口：

1. **Group A — nested namespace 共享归一化 + 流式缓冲收敛**
   - `bridge_context/` + `chat_to_responses/tools.py` + `stream_state/tools.py` + `stream_state/tool_items.py` + `tests/test_nested_namespace_tools.py`
2. **Group B — stream replay / message item id / assistant persistence 收口**
   - `stream_responses_state.py` + `stream_state/envelope.py` + `tests/test_phase2_regression.py`
3. **Group C — explicit namespace tool_choice thinking-mode 兼容收口**
   - `upstream_compat.py` + `tests/test_upstream_compat.py`

详见：`docs/phase-a-worktree-closure-checklist.md`

### 2.4 当前主风险

1. **结构性风险仍大于协议性风险**
   - 协议高价值主链路已基本闭环，后续主要风险在工程韧性与长期维护复杂度
2. **兼容逻辑继续散长的风险**
   - `upstream_compat.py` 持续增长，如不收敛为规则化结构，后续会再次变成 patch bucket
3. **文档与现实漂移风险**
   - README 当前仍写 `153 tests`，而 live 已是 `197 passed`
4. **工作树尚未收口**
   - 当前已有多文件未提交，需要进入“可提交、可审计、可回滚”的交付化整理阶段

---

## 3. 范围与非范围

## 3.1 本次重构范围

### A. 核心代码结构重构
- `responses_to_chat/request.py`
- `chat_to_responses/tools.py`
- `stream_state/tools.py`
- `stream_state/tool_items.py`
- `stream_responses_state.py`
- `upstream.py`
- `upstream_compat.py`
- `bridge_context/`
- `protocol/session.py`（如为兼容矩阵或持久化一致性需要）
- `api/response_service.py`（如需统一错误与 smoke boundary）

### B. 测试重构与测试矩阵收口
- `tests/test_reasoning_policy.py`
- `tests/test_nested_namespace_tools.py`
- `tests/test_phase2_regression.py`
- `tests/test_upstream_compat.py`
- 其他与 stream / tool / session / compat 直接相关测试

### C. 文档与交付物同步
- `README.md`
- `ARCHITECTURE.md`
- `REFACTOR_BENCHMARK.md`
- `docs/reasoning-policy-freeze.md`
- 新增：兼容矩阵 / rollout / smoke 文档

## 3.2 非范围

本轮明确不做：

- 把 bridge 扩展为多上游路由器
- 在 bridge 中做 provider capability center
- 扩展 admin / control plane / UI
- 把模型聚合职责从 NewAPI 搬回 bridge
- 无实际 ROI 的协议族大扩张（如与当前单上游场景无关的外围 item family）

---

## 4. 设计原则（本次重构冻结）

1. **主价值优先级**：结构收敛 > 测试闭环 > 文档同步 > 发布交付
2. **不再接受补丁堆叠**：同一职责若已有 3+ 层兼容分支，优先重写局部模块
3. **兼容逻辑集中化**：provider / alias / thinking / tool_choice 兼容应集中于明确边界，不分散在 request builder 与 stream state
4. **协议边界清晰化**：
   - request builder = 语义收集
   - compat policy = upstream-specific retry / degrade
   - stream state = 事件排序 / item buffering / finalization
   - session = persistence / replay
5. **真实验证优先于静态合理性**：能用真实 CLI / alias surface 证实的，不用猜

---

## 5. 分阶段实施计划

## Phase A — 工作树收口与结构重构基线

### 目标
把当前已验证通过但仍分散的变更整理成可审计的结构基线。

### 要做的事

1. **收敛 bridge_context / nested namespace 相关职责**
   - 固化 `bridge_context/nested_namespace.py` 作为唯一 normalizer
   - 检查 `ToolSpec` / namespace strategy 字段是否还存在重复或死面

2. **收敛 tool path 职责**
   - `chat_to_responses/tools.py`：仅做 non-streaming item restoration
   - `stream_state/tools.py`：仅做 buffering / emit timing / state transition
   - `tool_items.py`：仅做 item shape construction

3. **收敛 explicit tool_choice compat 路径**
   - 保持 `upstream_compat.py` 作为唯一兼容回退入口
   - 避免把 thinking/tool_choice 兼容判断回流到 request builder

4. **整理 dirty worktree 为一轮结构提交候选**
   - 保证每一处改动都能说清“属于哪类结构收敛”

### 交付物
- 一组结构性代码变更
- 测试仍全绿
- 变更可按主题切 commit

### 验证
```bash
cd /opt/codex-chat-bridge
.venv/bin/pytest -q
systemctl restart codex-chat-bridge.service
curl -fsS http://127.0.0.1:18090/health
```

---

## Phase B — 协议兼容矩阵与回归测试闭环

### 目标
把“已经靠真实验证证明过”的路径整理成可重复执行的工程矩阵，而不是口头结论。

### 要做的事

1. **建立最小生产兼容矩阵**
   - 模型维度：
     - `deepseek-v4-flash-codex`
     - `glm-5.1-codex`
     - 必要时 `glm-5.2-codex`
   - 场景维度：
     - 非流式 responses
     - 流式 responses
     - `previous_response_id`
     - 普通 function tool
     - nested namespace oneof
     - nested namespace anyof
     - explicit namespace `tool_choice`

2. **把高价值 real-world 失败归档成回归测试**
   - `glm-5.1-codex` alias target EOL 事故
   - `deepseek-v4-flash` explicit tool_choice vs thinking mode 冲突
   - stream assistant replay content normalization
   - message item id `msg_` continuity

3. **把 live probe 命令收敛成 smoke checklist**
   - raw bridge
   - NewAPI alias-surface
   - Hermes CLI

### 建议新增文档
- `docs/compatibility-smoke-matrix.md`
- `docs/alias-surface-validation.md`
- `docs/production-smoke.md`

### 当前 Phase B 进展（2026-07-02）

已完成：

- `docs/compatibility-smoke-matrix.md`
- `docs/alias-surface-validation.md`
- `docs/production-smoke.md`
- 关键 smoke 复核：
  - `deepseek-v4-flash-codex`：当前 PASS（alias visible, `/v1/responses` PASS, CLI PASS, tool PASS, continuation PASS, nested namespace PASS, explicit tool_choice PASS）
  - `glm-5.1-codex`：当前 BLOCKED（`/v1/models` 不可见，CLI / `/v1/responses` 返回 `No available channel for model glm-5.1-codex under group default`）

### 验证
至少保留下面三条作为生产 smoke：

```bash
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请只回复 OK'
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请使用一个工具读取 /etc/hostname，并且最终只输出主机名，不要附加解释。'
hermes chat --provider custom:newapi -m glm-5.1-codex -Q --yolo -q '请只回复 OK'
```

---

## Phase C — 文档同步与生产交付文档集

### 目标
让项目文档成为当前 live verified workflow，而不是历史快照。

### 要做的事

1. **更新 README**
   - 测试数改为 live 值
   - 加入 alias-surface 验证说明
   - 增加“生产 smoke”节

2. **更新 ARCHITECTURE**
   - 纳入 `nested_namespace.py`
   - 纳入 explicit tool_choice compat retry
   - 标出 compat retry 的职责边界

3. **处理 REFACTOR_BENCHMARK**
   - 保留为 historical reference 即可
   - 明确所有当前事实以 `ARCHITECTURE.md` 为准

4. **更新 reasoning freeze**
   - 加一条：explicit tool_choice thinking-mode incompatibility 的边界说明

5. **新增生产交付文档**
   - `docs/release-rollout-checklist.md`
   - `docs/production-smoke.md`
   - `docs/refactor-delivery-audit.md`

### 验证
- 文档中所有测试数、文件名、路径、行为都与 live code 一致
- `grep` / `read_file` 检查无明显旧结构残留

---

## Phase D — 提交策略与发布收口

### 目标
把本轮工作从“工作树修改”变为“可回滚交付”。

### 推荐提交切分

1. **refactor(protocol): unify nested namespace normalizer and stream tool buffering**
2. **fix(compat): support explicit namespace tool_choice under deepseek thinking-mode conflict**
3. **test(protocol): add nested namespace stream and upstream compat regressions**
4. **docs: sync architecture, README, freeze docs, and production smoke plan**

### 发布前核对
- `git diff --stat` 清晰
- 每个 commit 都能单独解释
- 全量测试绿
- systemd 重启后 smoke 绿

### 回滚策略
- 代码回滚：`git revert <commit>` 或恢复到当前基线提交
- 服务回滚：`systemctl restart codex-chat-bridge.service`
- alias 层问题：优先检查 `/opt/cliproxyapi/config.yaml` 映射，再重建 `cli-proxy-api`

---

## 6. 验证清单（生产交付版）

## 6.1 单元/集成验证
```bash
cd /opt/codex-chat-bridge
.venv/bin/pytest -q
```
目标：全绿

## 6.2 服务验证
```bash
systemctl restart codex-chat-bridge.service
curl -fsS http://127.0.0.1:18090/health
```
目标：服务健康

## 6.3 alias-surface 验证
```bash
curl -fsS http://127.0.0.1:8317/v1/models -H 'Authorization: Bearer sk-luckyss'
```
目标：关键 alias 可见

## 6.4 真实 CLI 验证
```bash
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请只回复 OK'
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请使用一个工具读取 /etc/hostname，并且最终只输出主机名，不要附加解释。'
hermes chat --provider custom:newapi -m glm-5.1-codex -Q --yolo -q '请只回复 OK'
```
目标：真实 agent loop 通过

---

## 7. 已知未决项（不阻断本轮计划）

1. `image_generation` built-in tool 在某些日志里仍有 warning
   - 当前不阻断 alias-surface 与主链路验证
2. 更广覆盖的 Responses item-family
   - 当前不是生产交付阻断项，属于后续增量范围
3. provider-specific 长尾 compat
   - 继续遵循“真实失败再加规则”，不做先验大扩张

---

## 8. 执行顺序建议

建议按下面顺序推进，不要交叉打散：

1. **收口当前 dirty worktree 结构改动**
2. **补齐兼容矩阵与 smoke 文档**
3. **同步 README / ARCHITECTURE / freeze docs**
4. **整理 commit 切分并本地提交**
5. **做一轮发布前全量验证**

---

## 9. 当前判断

当前项目不再处于“协议主能力缺失”阶段，而处于：

> **“协议主链路基本闭环，下一步应以结构收敛 + 测试矩阵 + 文档同步 + 发布收口为核心”的生产交付阶段。**

这意味着后续工作的衡量标准，不再是“又支持了一个小 case”，而是：

- 结构是否更清晰
- 回归是否更稳
- 文档是否更同步
- 发布是否更可审计
