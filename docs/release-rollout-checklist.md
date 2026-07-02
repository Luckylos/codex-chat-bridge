# Release Rollout Checklist

_更新时间：2026-07-02_

## 1. 目标

用于 codex-chat-bridge 在本地发布收口前进行最终核对。

当前口径：

- 本项目当前只做 **本地收口 / 本地验证 / 本地提交**
- **未执行远端 push / PR / deploy**
- 当前主验收 canary：`deepseek-v4-flash-codex`
- `glm-5.1-codex` 已降级为历史参考，不纳入当前验收

---

## 2. 发布前必查

### 2.1 Git 状态
- [x] 工作树 clean
- [x] 提交链清晰
- [x] Phase A/B/C/D 相关提交均已本地落库

### 2.2 测试
- [x] `pytest -q` 全绿
- [x] 当前基线：`246 passed, 1 warning`

### 2.3 服务健康
- [x] `systemctl restart codex-chat-bridge.service`
- [x] `GET /health` 返回 `ok=true`

### 2.4 Top-layer canary
- [x] `hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请只回复 OK'`
- [x] `hermes chat --provider custom:newapi -m deepseek-v4-flash-codex -Q --yolo -q '请使用一个工具读取 /etc/hostname，并且最终只输出主机名，不要附加解释。'`
- [x] explicit namespace `tool_choice` raw bridge stream probe（`model=deepseek-v4-flash`）返回 `shell` + `{"command":"pwd"}`

---

### 3. 当前建议发布提交链

```text
5587e41 refactor(protocol): unify nested namespace normalization and streaming buffering
2dccb79 fix(stream): preserve replay-safe assistant content and stable message item ids
a608bb7 fix(compat): support explicit namespace tool_choice under thinking-mode conflict
baea3cc docs: add phase-a closure checklist and production refactor delivery plan
740719d docs: add phase-b smoke matrix and alias-surface validation baseline
e549953 docs: de-scope glm-5.1-codex from current phase-b acceptance
8e70c1c docs: sync readme architecture and freeze docs for phase-c baseline
...
30c8fdf fix(stream): preserve chat-side nested tool calls for session replay
ac92b9c test(stream): lock replay and save-path shape regressions
ff25e46 test(stream): lock tool-search replay and request-echo regressions
```

> 说明：Phase D 之后，post-closure ladder 又补了 stream/session fidelity 收口提交；当前仍未包含远端 push。

---

## 4. 回滚策略

### 4.1 单提交回滚
```bash
git revert <commit>
```

### 4.2 服务回滚
```bash
systemctl restart codex-chat-bridge.service
```

### 4.3 本地发布前回退基线
如需整体回退，可基于以下提交逐笔回退：
- `8e70c1c`
- `e549953`
- `740719d`
- `baea3cc`
- `a608bb7`
- `2dccb79`
- `5587e41`

---

## 5. 当前不做

- 不做远端 push
- 不做 PR
- 不恢复 `glm-5.1-codex`
- 不扩展新的协议族范围

---

## 6. 一句话结论

> 当前代码、服务、文档、smoke 已达到本地发布收口标准；如需进入真正发布动作，下一步应是显式授权 push / tag / rollout。