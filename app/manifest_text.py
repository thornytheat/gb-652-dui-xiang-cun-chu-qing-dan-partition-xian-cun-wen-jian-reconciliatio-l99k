"""清单文本的行结构辅助。

随仓样例把整份清单存成一行、行间用字面 ``\\n`` 连接。文本完全没有真实
换行时才尝试展开，并且要求展开后的首行确实像清单（JSONL 的 ``{`` 或带
``key`` 表头的 CSV），避免误改原生文件名里的反斜杠。
"""
from __future__ import annotations


def expand_escaped_lines(text: str) -> str:
    if not isinstance(text, str):
        return text
    if "\n" in text or "\r" in text:
        return text
    if "\\n" not in text and "\\r" not in text:
        return text
    expanded = (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
    )
    first = expanded.split("\n", 1)[0].lstrip("﻿ \t")
    if first.startswith("{"):
        return expanded
    cells = {cell.strip().lstrip("﻿") for cell in first.split(",")}
    if "key" in cells:
        return expanded
    return text
