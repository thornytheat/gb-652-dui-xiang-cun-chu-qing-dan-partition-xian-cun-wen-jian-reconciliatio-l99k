"""清单来源驱动的路径解释规则。

同一个原键在不同清单来源下含义不同：

* ``supplier``（供货方）：反斜杠是分隔符，连续斜杠折叠，路径段按 URL
  百分号转义解码。
* ``native``（原生）：百分号、连续斜杠和反斜杠都是文件名的字面字符。

现存对象始终按 ``native`` 字面解释，避免把真实的 ``a%2Fb`` 与 ``a/b``
串成同一个对象。
"""
from __future__ import annotations

import re
from urllib.parse import unquote

from app.errors import DomainError

_BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")

POLICIES = {
    "supplier": {
        "decode": True,
        "backslash_separator": True,
        "collapse_slashes": True,
    },
    "native": {
        "decode": False,
        "backslash_separator": False,
        "collapse_slashes": False,
    },
}

OBJECT_SOURCE = "native"

ALIASES = {
    "supplier": "supplier",
    "vendor": "supplier",
    "escaped": "supplier",
    "escape": "supplier",
    "url": "supplier",
    "供货方": "supplier",
    "native": "native",
    "literal": "native",
    "raw": "native",
    "direct": "native",
    "原生": "native",
}

MARKERS = {"_EMPTY", "_SUCCESS"}


def canonical_source(source) -> str:
    if not isinstance(source, str) or not source.strip():
        raise DomainError("UNKNOWN_SOURCE", "清单来源为空或未登记")
    key = source.strip().lower()
    if key not in ALIASES:
        raise DomainError("UNKNOWN_SOURCE", f"清单来源未登记: {source}")
    return ALIASES[key]


def policy(source) -> dict:
    return POLICIES[canonical_source(source)]


def _decode_segment(segment: str) -> str:
    if _BAD_ESCAPE.search(segment):
        # urllib 会把孤立的 % 原样放过；供货方写法里这是残缺转义，隔离。
        raise ValueError("INVALID_ESCAPE")
    try:
        return unquote(segment, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("INVALID_ESCAPE") from exc


def split_key(raw_key: str, source="supplier") -> dict:
    """把原键拆成路径段；原键逐字保留在 ``raw_key``。

    解码结果里的 ``/``（例如供货方的 ``%2F``）会构成真实的目录层级，
    因此解释完成后再按目标键重新分段，保证分区归属与目标键一致。
    """
    if not isinstance(raw_key, str):
        raise ValueError("INVALID_KEY")
    rules = policy(source)
    text = raw_key
    if rules["backslash_separator"]:
        text = text.replace("\\", "/")
    is_dir = text.endswith("/")
    segments = text.split("/")
    if rules["collapse_slashes"]:
        segments = [segment for segment in segments if segment]
    if rules["decode"]:
        segments = [_decode_segment(segment) for segment in segments]
    target = "/".join(segments)
    if is_dir and segments and rules["collapse_slashes"]:
        # 供货方写法折叠后丢掉了尾部斜杠，这里补回目录证据。
        target += "/"
    segments = target.split("/") if target else []
    if segments and segments[-1] == "":
        segments = segments[:-1]
    return {
        "raw_key": raw_key,
        "source": canonical_source(source),
        "segments": segments,
        "target": target,
        "is_dir": is_dir,
    }


def interpret_key(raw_key: str, source="supplier") -> dict:
    return split_key(raw_key, source)


def partition_pairs(segments: list[str], is_dir: bool = False) -> list[tuple[str, str]] | None:
    """提取目录段中的有序分区键值对。

    最后一个路径段是文件名或标记，即使含 ``=`` 也不当作分区；``is_dir``
    为真时整个路径都是目录，全部参与判定。同名字段取值冲突时返回
    ``None``，由调用方隔离该行。
    """
    candidates = segments if is_dir else segments[:-1]
    pairs: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for segment in candidates:
        if not segment or "=" not in segment:
            continue
        name, value = segment.split("=", 1)
        if not name:
            continue
        if name in seen:
            if seen[name] != value:
                return None
            continue
        seen[name] = value
        pairs.append((name, value))
    return pairs


def partition_values(segments: list[str], is_dir: bool = False) -> dict:
    pairs = partition_pairs(segments, is_dir)
    return dict(pairs) if pairs is not None else {}


def partition_of(target: str) -> str:
    """对象所属分区标签。根目录统一记作 ``(root)``。"""
    if target == "":
        return "(root)"
    if target.endswith("/"):
        trimmed = target.rstrip("/")
        return trimmed or "(root)"
    if "/" in target:
        return target.rsplit("/", 1)[0] or "(root)"
    return "(root)"


def basename(target: str) -> str:
    return target.rstrip("/").rsplit("/", 1)[-1] if target else ""


def kind_of(target: str) -> str:
    if target.endswith("/"):
        return "directory"
    name = basename(target)
    if name in MARKERS:
        return "marker"
    return "object"
