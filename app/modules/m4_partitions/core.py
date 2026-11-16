"""M4 分区判定。

清单路径的解释方式由清单来源决定：

* ``supplier``：供货方转义写法。反斜杠是路径分隔符，连续斜杠折叠，
  每个路径段按 URL 百分号转义解码。
* ``native``：原生写法。百分号、连续斜杠和反斜杠都是文件名的字面字符，
  原样保留。

现存对象清单始终按字面解释（``literal``），不套用清单来源的规则。这样
供货方的 ``a%2Fb`` 能落到真实对象 ``a/b``，而原生清单里的 ``a%2Fb`` 仍
是另一个对象，不会被统一解码串到一起。

同一目标（解释后的键）若来自不同原键或不同元数据，视为冲突映射，相关
行全部隔离，不挑选一条覆盖。隔离行可以在修正映射后单独重放；已通过行
不参与重放，分区归属按通过行的去重集合重新计算，重试不会重复计数。
"""
from __future__ import annotations

import copy
import csv
import io
import json

from app.errors import DomainError
from app.manifest import canonical_digest
from app.manifest_text import expand_escaped_lines
from app.paths import (
    OBJECT_SOURCE,
    basename as _basename,
    canonical_source,
    interpret_key,
    kind_of as _kind,
    partition_of,
    partition_pairs,
)
from app.reasons import reason as reason_text

# ------------------------------------------------------------------
# 旧版分区判定（保留既有接口与行为）
# ------------------------------------------------------------------

SAMPLE = {
    "keys": [
        "dt=2026-11-15/hour=00/a",
        "dt=2026-11-15/hour=01/b",
        "dt=2026-11-15/hour=03/_EMPTY",
    ],
    "expected_hours": ["00", "01", "02", "03"],
}


def classify(keys, expected_hours):
    present = set()
    empty = set()
    duplicates = set()
    for key in keys:
        hour = next((x.split("=", 1)[1] for x in key.split("/") if x.startswith("hour=")), None)
        if hour in present:
            duplicates.add(hour)
        if hour:
            present.add(hour)
        if key.endswith("/_EMPTY") and hour:
            empty.add(hour)
    return {
        "present": sorted(present),
        "empty": sorted(empty),
        "missing": sorted(set(expected_hours) - present),
        "duplicates": sorted(duplicates),
    }


def run(payload):
    d = SAMPLE | payload
    out = classify(d["keys"], d["expected_hours"])
    return {
        "status": "READY",
        "input_count": len(d["keys"]),
        "valid_count": len(out["present"]),
        "quarantine_count": len(out["duplicates"]),
        **out,
        "reason": "GAP" if out["missing"] else "OK",
    }


# ------------------------------------------------------------------
# 来源与解释策略（定义在 app.paths，供 M2/M4 共用）
# ------------------------------------------------------------------

normalize_key = interpret_key

__all__ = [
    "SAMPLE", "classify", "run",
    "canonical_source", "interpret_key", "normalize_key",
    "partition_pairs", "partition_of", "build_batch", "replay", "summary",
    "empty_summary", "OBJECT_SOURCE",
]


# ------------------------------------------------------------------
# 清单读取
# ------------------------------------------------------------------

_KNOWN_COLUMNS = {"key", "size", "etag"}
_OPTIONAL_COLUMNS = {"source"}
_CSV_COLUMNS = _KNOWN_COLUMNS | _OPTIONAL_COLUMNS


def _guess_format(text: str, fmt: str | None) -> str:
    if fmt and fmt != "auto":
        return fmt
    return "jsonl" if text.lstrip("﻿ \t\r\n").startswith("{") else "csv"


def _text_entries(text: str, fmt: str):
    """读取清单文本，保留原键、物理行号与逐行来源。"""
    text = expand_escaped_lines(text)
    if not text.strip():
        return [], "empty-file"
    fmt = _guess_format(text, fmt)
    lines = text.splitlines()
    entries = []
    order = 0
    if fmt == "jsonl":
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                order += 1
                entries.append({
                    "order": order, "source_line": line_no, "raw_key": None,
                    "size": None, "etag": None, "row_source": None,
                    "pre_reason": None, "pre_state": "empty", "raw_value": line,
                })
                continue
            order += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                entries.append({
                    "order": order, "source_line": line_no, "raw_key": None,
                    "size": None, "etag": None, "row_source": None,
                    "pre_reason": "TRUNCATED", "pre_state": None, "raw_value": line,
                })
                continue
            if not isinstance(row, dict):
                entries.append({
                    "order": order, "source_line": line_no, "raw_key": None,
                    "size": None, "etag": None, "row_source": None,
                    "pre_reason": "INVALID_VALUE", "pre_state": None, "raw_value": line,
                })
                continue
            raw_key = row.get("key", row.get("raw_key"))
            reason = None if isinstance(raw_key, str) and raw_key != "" else "EMPTY_VALUE"
            entries.append({
                "order": order, "source_line": line_no, "raw_key": raw_key,
                "size": row.get("size"), "etag": row.get("etag"),
                "row_source": row.get("source"),
                "pre_reason": reason, "pre_state": None, "raw_value": raw_key,
            })
        state = "data" if entries else "empty-file"
        return entries, state

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], "empty-file"
    header = [cell.lstrip("﻿").strip() for cell in header]
    if not header or not set(header):
        return [], "empty-file"
    previous_line = reader.line_num
    for cells in reader:
        line_no = previous_line + 1
        previous_line = reader.line_num
        order += 1
        raw_line = lines[line_no - 1] if line_no - 1 < len(lines) else ""
        if not cells or all(cell.strip() == "" for cell in cells):
            entries.append({
                "order": order, "source_line": line_no, "raw_key": None,
                "size": None, "etag": None, "row_source": None,
                "pre_reason": None, "pre_state": "empty", "raw_value": raw_line,
            })
            continue
        row = dict(zip(header, cells))
        if (
            len(cells) != len(header)
            or "key" not in header
            or not _KNOWN_COLUMNS <= set(header)
            or not set(header) <= _CSV_COLUMNS
        ):
            entries.append({
                "order": order, "source_line": line_no, "raw_key": None,
                "size": None, "etag": None, "row_source": None,
                "pre_reason": "BAD_COLUMNS", "pre_state": None, "raw_value": raw_line,
            })
            continue
        raw_key = row.get("key")
        reason = None if isinstance(raw_key, str) and raw_key.strip() != "" else "EMPTY_VALUE"
        entries.append({
            "order": order, "source_line": line_no, "raw_key": raw_key,
            "size": row.get("size"), "etag": row.get("etag"),
            "row_source": row.get("source") or None,
            "pre_reason": reason, "pre_state": None, "raw_value": raw_key,
        })
    if not entries:
        return [], "only-header"
    return entries, "data"


def _list_entries(rows):
    entries = []
    for index, item in enumerate(rows, 1):
        if isinstance(item, str):
            item = {"key": item}
        if not isinstance(item, dict):
            entries.append({
                "order": index, "source_line": index, "raw_key": None,
                "size": None, "etag": None, "row_source": None,
                "pre_reason": "INVALID_VALUE", "pre_state": None, "raw_value": item,
            })
            continue
        raw_key = item.get("key", item.get("raw_key"))
        if raw_key is None and not item:
            entries.append({
                "order": index, "source_line": index, "raw_key": None,
                "size": None, "etag": None, "row_source": None,
                "pre_reason": None, "pre_state": "empty", "raw_value": item,
            })
            continue
        entries.append({
            "order": index,
            "source_line": item.get("source_line", index),
            "raw_key": raw_key,
            "size": item.get("size"),
            "etag": item.get("etag"),
            "row_source": item.get("source"),
            "pre_reason": None,
            "pre_state": None,
            "raw_value": raw_key,
        })
    return entries, "data" if entries else "empty-file"


def _load_manifest(payload):
    for name in ("manifest", "keys", "lines"):
        value = payload.get(name)
        if value is None:
            continue
        if isinstance(value, str):
            return _text_entries(value, payload.get("format", "auto"))
        if isinstance(value, dict):
            value = [value]
        return _list_entries(value)
    value = payload.get("text")
    if value is not None:
        return _text_entries(value, payload.get("format", "auto"))
    return [], "empty-file"


def _load_objects(payload):
    """现存对象按字面保留，不做任何解码或分隔符折叠。"""
    for name in ("objects", "object_keys", "actual"):
        if name in payload:
            value = payload.get(name)
            break
    else:
        return None
    if value is None:
        return None
    if isinstance(value, str):
        return [line for line in value.splitlines() if line != ""]
    keys = []
    for item in value:
        if isinstance(item, str):
            keys.append(item)
        elif isinstance(item, dict):
            key = item.get("key", item.get("raw_key"))
            if key is not None:
                keys.append(key)
    return keys


def _load_expected_hours(payload):
    for name in ("expected_hours", "hours"):
        value = payload.get(name)
        if value is not None:
            break
    else:
        return []
    if isinstance(value, str):
        return [part for part in value.replace(",", " ").split() if part]
    return [str(item) for item in value]


def _signature(row):
    return (
        row.get("source"),
        row.get("raw_key"),
        json.dumps(row.get("size"), sort_keys=True, default=str),
        json.dumps(row.get("etag"), sort_keys=True, default=str),
    )


# ------------------------------------------------------------------
# 行解释与冲突裁决
# ------------------------------------------------------------------

def _evaluate_row(row, source, target_override=None, objects=None):
    """解释一行。成功时状态为 candidate，失败时直接 quarantined。

    提供 ``target_override`` 时按人工修正后的映射判定，即使原键缺失也
    可以修复隔离行；原键仍原样保留在 ``raw_key``。
    """
    raw_key = row.get("raw_key")
    if target_override is None and not isinstance(raw_key, str):
        return {"status": "quarantined", "reason": "INVALID_VALUE", "target": None,
                "normalized_value": None, "partitions": {}, "partition": None,
                "kind": None, "object_found": None}

    if target_override is not None:
        if not isinstance(target_override, str):
            return {"status": "quarantined", "reason": "INVALID_VALUE", "target": None,
                    "normalized_value": None, "partitions": {}, "partition": None,
                    "kind": None, "object_found": None}
        target = target_override
        segments = target.split("/")
        if segments and segments[-1] == "":
            segments = segments[:-1]
            target = target_override
        is_dir = target_override.endswith("/")
        effective_source = row.get("source") or source
    else:
        try:
            interpreted = interpret_key(raw_key, source)
        except DomainError:
            return {"status": "quarantined", "reason": "UNKNOWN_SOURCE", "target": None,
                    "normalized_value": None, "partitions": {}, "partition": None,
                    "kind": None, "object_found": None}
        except ValueError:
            return {"status": "quarantined", "reason": "INVALID_ESCAPE", "target": None,
                    "normalized_value": None, "partitions": {}, "partition": None,
                    "kind": None, "object_found": None}
        target = interpreted["target"]
        segments = interpreted["segments"]
        is_dir = interpreted.get("is_dir", False)
        effective_source = interpreted["source"]

    if target == "":
        return {"status": "quarantined", "reason": "EMPTY_KEY", "target": None,
                "normalized_value": None, "partitions": {}, "partition": None,
                "kind": None, "object_found": None, "source": effective_source}

    pairs = partition_pairs(segments, is_dir)
    if pairs is None:
        return {"status": "quarantined", "reason": "CONFLICTING_PARTITION",
                "target": target, "normalized_value": target, "partitions": {},
                "partition": partition_of(target), "kind": _kind(target),
                "object_found": None, "source": effective_source}

    found = None
    if objects is not None:
        found = target in set(objects)
    return {
        "status": "candidate",
        "reason": "OK",
        "target": target,
        "normalized_value": target,
        "partitions": dict(pairs),
        "partition": partition_of(target),
        "kind": _kind(target),
        "object_found": found,
        "source": effective_source,
    }

def _resolve_conflicts(rows, passed_targets=None):
    """同一目标只允许一个来源。

    不同原键或不同元数据落到同一目标时属于冲突映射：相关行全部隔离，
    不挑选一条覆盖。完全相同的重复行视为重复上报，保留最早一行。
    """
    passed_targets = set(passed_targets or ())
    groups = {}
    for row in rows:
        if row["status"] == "candidate":
            groups.setdefault(row["target"], []).append(row)
    for target, group in groups.items():
        if target in passed_targets:
            for row in group:
                row["status"] = "quarantined"
                row["reason"] = "CONFLICTING_MAPPING"
            continue
        if len(group) == 1:
            group[0]["status"] = "passed"
            group[0]["reason"] = "OK"
            continue
        signatures = {_signature(row) for row in group}
        if len(signatures) == 1:
            ordered = sorted(group, key=lambda r: (r.get("source_line", 0), r.get("row_id", "")))
            ordered[0]["status"] = "passed"
            ordered[0]["reason"] = "OK"
            for row in ordered[1:]:
                row["status"] = "quarantined"
                row["reason"] = "DUPLICATE_ROW"
        else:
            for row in group:
                row["status"] = "quarantined"
                row["reason"] = "CONFLICTING_MAPPING"
    return rows


def _finish_row(row):
    row["raw_value"] = row.get("raw_key") if row.get("raw_key") is not None else row.get("raw_value")
    row["reason_code"] = row.get("reason")
    row["reason_text"] = reason_text(row.get("reason") or "OK")
    if row.get("target") is not None:
        row["normalized_value"] = row["target"]
    row.setdefault("attempts", 0)
    return row

def build_batch(payload: dict) -> dict:
    """从导入载荷构造批次状态（纯函数，不落库）。"""
    requested_source = payload.get("source")
    batch_source = canonical_source(requested_source if requested_source is not None else "supplier")
    entries, manifest_state = _load_manifest(payload)
    objects = _load_objects(payload)
    expected_hours = _load_expected_hours(payload)

    digest_payload = {
        "source": batch_source,
        "rows": [
            {
                "source_line": entry["source_line"],
                "key": entry["raw_key"],
                "size": entry["size"],
                "etag": entry["etag"],
                "source": entry["row_source"],
                "reason": entry["pre_reason"],
            }
            for entry in entries
        ],
        "objects": objects,
        "expected_hours": expected_hours,
    }
    digest = canonical_digest(digest_payload)
    batch_id = "pb-" + digest[:12]

    rows = []
    for index, entry in enumerate(entries, 1):
        row_id = f"R{index:04d}"
        row = {
            "row_id": row_id,
            "source_line": entry["source_line"],
            "raw_key": entry["raw_key"],
            "raw_value": entry["raw_value"],
            "size": entry["size"],
            "etag": entry["etag"],
            "source": None,
            "status": "quarantined",
            "reason": entry["pre_reason"] or "INVALID_VALUE",
            "attempts": 0,
            "target": None,
            "normalized_value": None,
            "partitions": {},
            "partition": None,
            "kind": None,
            "object_found": None,
        }
        if entry.get("pre_state") == "empty":
            row["status"] = "empty"
            row["reason"] = "EMPTY_ROW"
            rows.append(_finish_row(row))
            continue
        if entry["pre_reason"]:
            rows.append(_finish_row(row))
            continue

        row_source = entry["row_source"]
        if row_source is not None:
            try:
                row_source = canonical_source(row_source)
            except DomainError:
                row["reason"] = "UNKNOWN_SOURCE"
                rows.append(_finish_row(row))
                continue
        else:
            row_source = batch_source
        row["source"] = row_source

        evaluated = _evaluate_row(row, row_source, None, objects)
        row.update(evaluated)
        rows.append(_finish_row(row))

    _resolve_conflicts(rows)
    for row in rows:
        _finish_row(row)

    return {
        "batch_id": batch_id,
        "digest": digest,
        "source": batch_source,
        "objects": objects,
        "expected_hours": expected_hours,
        "manifest_state": manifest_state,
        "rows": rows,
        "replays": 0,
    }


# ------------------------------------------------------------------
# 重放
# ------------------------------------------------------------------

def _override_for(overrides, row):
    """按行定位修正映射。

    ``overrides`` 可以是：

    * 单个修正对象，含 ``row_id``/``raw_key``/``source_line`` 选择器；
    * ``{选择器: 修正}`` 字典，选择器为 row_id、原键或物理行号；
    * 修正对象列表；
    * ``{"*": 修正}`` 作为兜底。
    """
    if not overrides:
        return None
    if isinstance(overrides, dict):
        if _selector_matches(overrides, row):
            return overrides
        for key in (row.get("row_id"), row.get("raw_key"), str(row.get("source_line"))):
            if key is not None and key in overrides:
                return overrides[key]
        return overrides.get("*")
    if isinstance(overrides, list):
        for item in overrides:
            if isinstance(item, dict) and _selector_matches(item, row):
                return item
    return None


def _selector_matches(selector, row) -> bool:
    if not isinstance(selector, dict):
        return False
    if "row_id" in selector and selector["row_id"] == row.get("row_id"):
        return True
    if "raw_key" in selector and selector["raw_key"] == row.get("raw_key"):
        return True
    if "source_line" in selector and str(selector["source_line"]) == str(row.get("source_line")):
        return True
    return False


def _override_target(override):
    if override is None:
        return None
    if isinstance(override, str):
        return override
    if isinstance(override, dict):
        if "target" in override:
            return override["target"]
        if "key" in override:
            return override["key"]
    return None


def _override_source(override):
    if isinstance(override, dict) and override.get("source") is not None:
        return override["source"]
    return None


def _has_selector(overrides) -> bool:
    """修正映射是否按行定位（而不是全局 ``*`` 或纯来源重放）。

    只要给出的是 ``{选择器: 修正}`` / 含 row_id、raw_key、source_line 的
    修正对象 / 修正对象列表，就只处理被点名的行；``{"*": 修正}`` 才视为
    全局重放。
    """
    if isinstance(overrides, dict):
        keys = {str(key) for key in overrides}
        return bool(keys - {"*"})
    if isinstance(overrides, list):
        return bool(overrides)
    return False


def replay(batch: dict, overrides=None, source=None) -> dict:
    """仅重放隔离行；已通过行保持原样，分区按通过行去重集合重算。

    * 给出修正映射（row_id / 原键 / 行号 + target）时只处理被指定的隔离行，
      不会顺带改动同一冲突组的其他行；同组其他行仍保持隔离，供人工继续
      核对，避免悄悄放行未确认的行。
    * 只给出 ``source`` 或 ``{"*": ...}`` 时视为全局重放，重新解释全部隔离行。
    * 没有可重放行时保持原状，不重复累计，也不改变计数。
    """
    state = copy.deepcopy(batch)
    rows = state["rows"]
    passed = [row for row in rows if row["status"] == "passed"]
    passed_targets = {row["target"] for row in passed}
    passed_signatures = {}
    for row in passed:
        passed_signatures.setdefault(row["target"], set()).add(_signature(row))

    targeted = _has_selector(overrides)
    active = bool(overrides) or source is not None
    attempted = 0
    adopted = 0
    pending = []
    if active:
        for row in rows:
            if row["status"] != "quarantined":
                continue
            override = _override_for(overrides, row)
            target_override = _override_target(override)
            if targeted and override is None:
                # 单行修正只作用于被点名的行。
                continue
            # 结构性问题（缺列、截断）没有可解释的原键，单纯换来源无法修复；
            # 只有给出修正后的目标键时才尝试，避免覆盖原始证据。
            if target_override is None and not isinstance(row.get("raw_key"), str):
                continue
            try:
                merged_source = (
                    _override_source(override)
                    or source
                    or row.get("source")
                    or state["source"]
                )
                merged_source = canonical_source(merged_source)
            except DomainError as exc:
                # 重放失败不回写原因码，保留原始证据；失败信息挂在 last_error。
                row["attempts"] = int(row.get("attempts", 0)) + 1
                row["last_error"] = exc.code
                attempted += 1
                _finish_row(row)
                continue

            row["attempts"] = int(row.get("attempts", 0)) + 1
            attempted += 1
            evaluated = _evaluate_row(
                row, merged_source, target_override, state.get("objects")
            )
            row.update(evaluated)
            row.pop("last_error", None)
            if row["status"] == "candidate":
                pending.append(row)
            _finish_row(row)

    groups = {}
    for row in pending:
        groups.setdefault(row["target"], []).append(row)
    for target, group in groups.items():
        signatures = {_signature(row) for row in group}
        if target in passed_targets:
            for row in group:
                row["status"] = "quarantined"
                if _signature(row) in passed_signatures.get(target, set()):
                    row["reason"] = "DUPLICATE_ROW"
                else:
                    row["reason"] = "CONFLICTING_MAPPING"
                _finish_row(row)
        elif len(group) == 1:
            row = group[0]
            row["status"] = "passed"
            row["reason"] = "OK"
            _finish_row(row)
            adopted += 1
        elif len(signatures) == 1:
            ordered = sorted(group, key=lambda r: (r.get("source_line", 0), r.get("row_id", "")))
            ordered[0]["status"] = "passed"
            ordered[0]["reason"] = "OK"
            _finish_row(ordered[0])
            adopted += 1
            for row in ordered[1:]:
                row["status"] = "quarantined"
                row["reason"] = "DUPLICATE_ROW"
                _finish_row(row)
        else:
            for row in group:
                row["status"] = "quarantined"
                row["reason"] = "CONFLICTING_MAPPING"
                _finish_row(row)

    if attempted:
        state["replays"] = int(state.get("replays", 0)) + 1
    state["last_replay"] = {
        "attempted": attempted,
        "adopted": adopted,
        "still_quarantined": attempted - adopted,
        "noop": attempted == 0,
        "failed": sum(
            1 for row in rows
            if row.get("last_error") and row.get("status") == "quarantined"
        ),
    }
    return state


# ------------------------------------------------------------------
# 汇总
# ------------------------------------------------------------------

def summary(state: dict | None) -> dict:
    if not state:
        return empty_summary()
    rows = state.get("rows", [])
    passed = [row for row in rows if row.get("status") == "passed"]
    empty_rows = [row for row in rows if row.get("status") == "empty"]
    quarantined = [
        row for row in rows
        if row.get("status") not in {"passed", "empty"}
    ]

    partition_map = {}
    for row in passed:
        label = row.get("partition") or "(root)"
        entry = partition_map.setdefault(
            label, {"partition": label, "objects": set(), "markers": set()}
        )
        if row.get("kind") == "marker":
            entry["markers"].add(row.get("target"))
        else:
            entry["objects"].add(row.get("target"))

    partitions = []
    for label in sorted(partition_map):
        entry = partition_map[label]
        objects = sorted(entry["objects"])
        markers = sorted(entry["markers"])
        has_empty_marker = any(
            _basename(marker) == "_EMPTY" for marker in markers
        )
        if objects:
            part_state = "data"
        elif has_empty_marker:
            part_state = "empty"
        elif markers:
            part_state = "marker-only"
        else:
            part_state = "empty"
        partitions.append({
            "partition": label,
            "objects": objects,
            "markers": markers,
            "count": len(objects),
            "empty": part_state == "empty",
            "marker_only": part_state == "marker-only",
            "state": part_state,
        })

    present_hours = {
        row.get("partitions", {}).get("hour")
        for row in passed
        if row.get("partitions", {}).get("hour") and row.get("kind") != "marker"
    }
    empty_hours = {
        row.get("partitions", {}).get("hour")
        for row in passed
        if row.get("partitions", {}).get("hour")
        and row.get("kind") == "marker"
        and _basename(row.get("target") or "") == "_EMPTY"
    } - present_hours
    expected_hours = state.get("expected_hours") or []
    missing_hours = sorted(set(expected_hours) - present_hours - empty_hours)

    objects = state.get("objects")
    missing_objects = []
    orphan_objects = []
    if objects is not None:
        object_set = set(objects)
        passed_targets = {
            row.get("target") for row in passed if row.get("kind") != "marker"
        }
        missing_objects = sorted(passed_targets - object_set)
        orphan_objects = sorted(object_set - passed_targets)

    reason_codes = [row.get("reason") for row in quarantined]
    if "CONFLICTING_MAPPING" in reason_codes:
        reason = "CONFLICTING_MAPPING"
    elif reason_codes:
        reason = reason_codes[0]
    elif missing_hours:
        reason = "GAP"
    elif missing_objects:
        reason = "MISSING"
    else:
        reason = "OK"

    return {
        "status": "READY",
        "batch_id": state.get("batch_id"),
        "digest": state.get("digest"),
        "source": state.get("source"),
        "manifest_state": state.get("manifest_state", "data"),
        "replays": state.get("replays", 0),
        "input_count": len(rows),
        "valid_count": len(passed),
        "quarantine_count": len(quarantined),
        "empty_count": len(empty_rows),
        "partition_count": len(partitions),
        "expected_hours": expected_hours,
        "present_hours": sorted(present_hours),
        "empty_hours": sorted(empty_hours),
        "missing_hours": missing_hours,
        "objects_provided": objects is not None,
        "missing_objects": missing_objects,
        "orphan_objects": orphan_objects,
        "partitions": partitions,
        "assignments": passed,
        "quarantine": quarantined,
        "empty_rows": empty_rows,
        "rows": rows,
        "last_replay": state.get("last_replay"),
        "reason": reason,
        "reason_text": reason_text(reason),
    }


def empty_summary() -> dict:
    return {
        "status": "EMPTY",
        "batch_id": None,
        "digest": None,
        "source": None,
        "manifest_state": "empty",
        "replays": 0,
        "input_count": 0,
        "valid_count": 0,
        "quarantine_count": 0,
        "empty_count": 0,
        "partition_count": 0,
        "expected_hours": [],
        "present_hours": [],
        "empty_hours": [],
        "missing_hours": [],
        "objects_provided": False,
        "missing_objects": [],
        "orphan_objects": [],
        "partitions": [],
        "assignments": [],
        "quarantine": [],
        "empty_rows": [],
        "rows": [],
        "last_replay": None,
        "reason": "OK",
        "reason_text": reason_text("OK"),
    }
