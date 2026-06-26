"""SSE 帧解析与序列化 — 纯函数，无外部依赖，可独立测试。"""

from __future__ import annotations

import json
from typing import Any


def extract_block(buffer: str) -> tuple[str, str] | None:
    """从 buffer 中提取第一个完整的 SSE frame block。

    返回 (block, remaining_buffer) 或 None（没有完整帧）。
    帧分隔符为连续两个换行 \n\n。
    """
    marker = "\n\n"
    idx = buffer.find(marker)
    if idx == -1:
        return None
    return buffer[:idx], buffer[idx + len(marker):]


def parse_sse_block(block: str) -> tuple[str | None, str | None]:
    """解析一个 SSE block，提取 event 类型和 data 内容。

    返回 (event_name, data_string)，event_name 可能为空。
    """
    event_name: str | None = None
    data_parts: list[str] = []
    for line in block.splitlines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_parts.append(line.split(":", 1)[1].lstrip())
    data = "\n".join(data_parts) if data_parts else None
    return event_name, data


def parse_sse_json_block(block: str) -> tuple[str | None, dict | None]:
    """解析 SSE block 并尝试将 data 解析为 JSON。

    返回 (event_name, parsed_json_dict) 或 (event_name, None)。
    """
    event, data = parse_sse_block(block)
    if data:
        try:
            return event, json.loads(data)
        except json.JSONDecodeError:
            return event, None
    return event, None


def serialize_event(event: str | None, data: Any) -> bytes:
    """将单个 SSE 事件序列化为字节流。

    如果 event 为 None 或空，不写 event: 行。
    data 会被 JSON 序列化。
    """
    parts: list[str] = []
    if event:
        parts.append(f"event: {event}")
    parts.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    parts.append("")
    return ("\n".join(parts) + "\n").encode("utf-8")


def sse_event(event: str, data: Any) -> bytes:
    """快捷函数：生成一条带 event 名称的 SSE 事件。"""
    return serialize_event(event, data)


def sse_done() -> bytes:
    """生成 SSE [DONE] 终止标记。"""
    return b"data: [DONE]\n\n"


def iter_sse_bytes_as_list(
    chunks: list[str],
) -> list[tuple[str | None, dict | None]]:
    """从 SSE chunk 列表解析出 (event, parsed_data) 列表。

    用于测试和调试。生产环境使用 stream_chat_to_responses 序列化。
    """
    result: list[tuple[str | None, dict | None]] = []
    buffer = ""
    for chunk in chunks:
        buffer += chunk
        while True:
            extracted = extract_block(buffer)
            if extracted is None:
                break
            block, buffer = extracted
            if not block.strip():
                continue
            result.append(parse_sse_json_block(block))
    return result
