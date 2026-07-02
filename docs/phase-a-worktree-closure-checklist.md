# Phase A 工作树收口清单

_更新时间：2026-07-02_

## 1. 目标

本清单用于把当前 `/opt/codex-chat-bridge` 的未提交工作树收口为 **可解释、可测试、可提交、可回滚** 的结构性交付基线。

当前原则：

- 不再继续堆补丁
- 先把已经验证通过的改动按职责分组
- 每组必须有明确文件边界、验证命令、建议 commit message
- Phase A 只做 **工作树收口与提交边界明确化**，不在这一阶段扩展新的协议族范围

---

## 2. 当前工作树分组（建议提交切分）

## Group A — nested namespace 共享归一化 + 流式缓冲收敛

### 目的
把 nested namespace 的 action 解析规则从“多点重复逻辑”收敛为一个共享 normalizer，并让 stream state 只负责 buffering / emit timing。

### 文件

- `codex_chat_bridge/bridge_context/__init__.py`
- `codex_chat_bridge/bridge_context/models.py`
- `codex_chat_bridge/bridge_context/nested_namespace.py` _(new)_
- `codex_chat_bridge/chat_to_responses/tools.py`
- `codex_chat_bridge/stream_state/tool_items.py`
- `codex_chat_bridge/stream_state/tools.py`
- `tests/test_nested_namespace_tools.py`

### 这一组已覆盖的事实

- non-streaming namespace action restore 已通过
- `nested_oneof` / `nested_anyof` 真实流式路径已通过
- alias-surface（`deepseek-v4-flash-codex`）真实命中后：
  - `added_names` / `done_names` 为具体 action
  - `nested_anyof` `params` 已成功展平

### 建议 commit message

```bash
git add \
  codex_chat_bridge/bridge_context/__init__.py \
  codex_chat_bridge/bridge_context/models.py \
  codex_chat_bridge/bridge_context/nested_namespace.py \
  codex_chat_bridge/chat_to_responses/tools.py \
  codex_chat_bridge/stream_state/tool_items.py \
  codex_chat_bridge/stream_state/tools.py \
  tests/test_nested_namespace_tools.py

git commit -m "refactor(protocol): unify nested namespace normalization and streaming buffering"
```

### 收口后验证

```bash
cd /opt/codex-chat-bridge
.venv/bin/pytest -q tests/test_nested_namespace_tools.py
```

---

## Group B — stream replay / message item id / assistant persistence 收口

### 目的
把 stream assistant replay 与 message item id 连续性修复收拢成独立主题，避免与 nested buffering 混在一个提交里。

### 文件

- `codex_chat_bridge/stream_responses_state.py`
- `codex_chat_bridge/stream_state/envelope.py`
- `tests/test_phase2_regression.py`

### 这一组已覆盖的事实

- stream assistant message 持久化前会转回 Chat-compatible content
- `message_item_id` 采用 `msg_<response_id>` 新格式
- `previous_response_id` continuation 在真实链路上已通过

### 建议 commit message

```bash
git add \
  codex_chat_bridge/stream_responses_state.py \
  codex_chat_bridge/stream_state/envelope.py \
  tests/test_phase2_regression.py

git commit -m "fix(stream): preserve replay-safe assistant content and stable message item ids"
```

### 收口后验证

```bash
cd /opt/codex-chat-bridge
.venv/bin/pytest -q tests/test_phase2_regression.py
```

---

## Group C — explicit namespace tool_choice thinking-mode 兼容收口

### 目的
把 explicit `tool_choice` 的 thinking-mode 冲突支持明确收敛到 upstream compat retry 层，而不是散落到 request builder 或 nested parser。

### 文件

- `codex_chat_bridge/upstream_compat.py`
- `tests/test_upstream_compat.py` _(new)_

### 这一组已覆盖的事实

- 原始失败已复现：
  - `deepseek-v4-flash`
  - explicit `tool_choice` object
  - provider-default thinking mode
  - 上游返回 400
- compat retry 后已通过：
  - raw bridge
  - alias-surface `deepseek-v4-flash-codex`
  - explicit namespace tool_choice

### 建议 commit message

```bash
git add \
  codex_chat_bridge/upstream_compat.py \
  tests/test_upstream_compat.py

git commit -m "fix(compat): support explicit namespace tool_choice under thinking-mode conflict"
```

### 收口后验证

```bash
cd /opt/codex-chat-bridge
.venv/bin/pytest -q tests/test_upstream_compat.py tests/test_reasoning_policy.py
```

并做一次 live probe：

```bash
python3 - <<'PY'
import json, urllib.request
payload={
  'model':'deepseek-v4-flash',
  'input':'You must call the codex tool with shell action and command pwd. Do not answer with normal text.',
  'max_output_tokens':256,
  'tool_choice': {'type':'function','name':'codex','namespace':'codex'},
  'tools':[{
    'type':'namespace','name':'codex','strategy':'nested_oneof','tools':[
      {'type':'function','function':{'name':'shell','description':'Execute a shell command','parameters':{'type':'object','properties':{'command':{'type':'string'}},'required':['command']}}},
      {'type':'function','function':{'name':'apply_patch','description':'Apply a patch','parameters':{'type':'object','properties':{'patch':{'type':'string'}},'required':['patch']}}},
    ]
  }]
}
req=urllib.request.Request('http://127.0.0.1:18090/v1/responses', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req, timeout=180) as resp:
    print(resp.status)
    print(resp.read().decode())
PY
```

---

## 3. Phase A 总体验证

在三个 Group 都准备好后，执行完整验证：

```bash
cd /opt/codex-chat-bridge
.venv/bin/pytest -q
systemctl restart codex-chat-bridge.service
curl -fsS http://127.0.0.1:18090/health
```

建议再做两条 alias-surface smoke：

```bash
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请只回复 OK'
hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请使用一个工具读取 /etc/hostname，并且最终只输出主机名，不要附加解释。'
```

目标：

- pytest 全绿
- systemd 健康
- alias-surface smoke 通过

---

## 4. Phase A 完成判定

满足以下条件即可视为 Phase A 完成：

- [ ] 当前 dirty worktree 已按 3 个职责组划分清楚
- [ ] 每个组都有对应测试与 live probe
- [ ] 结构理由可以独立解释
- [ ] 提交顺序清晰，不混杂 unrelated docs / rollout 内容
- [ ] 全量测试与 systemd 验证通过

---

## 5. 进入 Phase B 前必须同步的事实

进入 Phase B 前，需把以下事实带过去：

1. `glm-5.1-codex` 事故根因是 alias target liveness，而不是 bridge 协议逻辑本身
2. `deepseek-v4-flash` explicit tool_choice 冲突根因是 thinking-mode 兼容，而不是 nested parser 本身
3. nested namespace 真正的结构主价值已经完成：
   - shared normalizer
   - stream buffering
   - oneof / anyof 真实流式验证
4. 下一阶段主任务应转为：
   - 兼容矩阵沉淀
   - smoke 文档化
   - README / ARCHITECTURE / freeze docs 同步

---

## 6. 当前建议

如果继续执行，不要再新增协议逻辑，直接按本清单完成：

1. Group A / B / C 提交切分
2. 全量验证
3. 准备进入 Phase B

也就是说：

> **Phase A 的正确收口方式，是把“已经验证通过的结构变更”清晰提交，而不是继续扩大功能面。**
