# codex-chat-bridge 重构基准文档（历史参考）

> **状态**: 🏛️ Historical reference — 此文档记录 Phase 2 之前的基准状态，用于重构规划参考。  
> **生成时间**: 2026-06-29  
> **基准版本**: pre-phase2 working tree  
> **当前版本**: Phase 5 审计后，153 测试全绿，67 项问题已修复  
>  
> ⚠️ 文件结构、行数、测试数均已被重构大幅改变，请以 `ARCHITECTURE.md` 为准。

---

## 1. 项目定位

codex-chat-bridge 是一个 **thin single-upstream protocol-conversion relay**：

- 职责：Responses API ↔ Chat Completions API 双向协议转换
- 上游：单一 NewAPI 实例（127.0.0.1:3000）
- 不做的事：多上游路由、模型聚合、provider 能力中心、未知字段透传、SDK-shape extra_body
