from __future__ import annotations

from app.errors import DomainError
from . import core

IMPORT_KEYS = {
    "manifest", "text", "lines", "objects", "object_keys", "actual",
    "source", "format",
}
LEGACY_KEYS = {"keys", "expected_hours"}


def _is_import(payload) -> bool:
    """旧样例载荷只有 keys/expected_hours，其余视为清单导入。"""
    if not isinstance(payload, dict):
        return False
    if IMPORT_KEYS & set(payload):
        return True
    return payload.get("keys") is not None and bool(set(payload) - LEGACY_KEYS)


def execute(payload: dict | None = None, store=None) -> dict:
    """兼容入口：旧样例载荷走原判定；清单载荷走批次导入。"""
    payload = payload or {}
    if _is_import(payload):
        return import_manifest(payload, store)
    return core.run(payload)


def import_manifest(payload: dict, store=None) -> dict:
    state = core.build_batch(payload)
    if store is not None and state["rows"]:
        # 空清单没有可核对的行，不落库，避免产生空批次。
        existing = store.find_batch_by_digest(state["digest"])
        if existing is not None:
            # 同一输入摘要重复导入：复用已保存批次，不新增分区结果。
            state = existing
        else:
            store.save_batch(state["batch_id"], state["digest"], state)
    return _present(state)


def get_batch(batch_id: str, store=None) -> dict:
    if store is None:
        raise DomainError("STORE_UNAVAILABLE", "批次存储不可用")
    state = store.load_batch(batch_id)
    if state is None:
        raise DomainError("BATCH_NOT_FOUND", f"批次不存在: {batch_id}")
    return _present(state)


def latest(store=None) -> dict:
    if store is None:
        return core.empty_summary()
    state = store.latest_batch()
    if state is None:
        return core.empty_summary()
    return _present(state)


def replay(batch_id: str, payload: dict | None = None, store=None) -> dict:
    if store is None:
        raise DomainError("STORE_UNAVAILABLE", "批次存储不可用")
    payload = payload or {}
    state = store.load_batch(batch_id)
    if state is None:
        raise DomainError("BATCH_NOT_FOUND", f"批次不存在: {batch_id}")

    overrides = payload.get("overrides")
    has_selector = any(
        key in payload for key in ("row_id", "raw_key", "source_line")
    )
    if overrides is None and (has_selector or "target" in payload):
        # 单行修正：source 随该行一起解释，不扩散到其他隔离行。
        overrides = payload
        source = None
    else:
        # 全局重放：只按来源重试所有隔离行。
        source = payload.get("source")
    state = core.replay(state, overrides=overrides, source=source)
    store.save_batch(state["batch_id"], state["digest"], state)
    return _present(state)


def _present(state: dict) -> dict:
    view = core.summary(state)
    view["assignments"] = [_row_view(row) for row in view["assignments"]]
    view["quarantine"] = [_row_view(row) for row in view["quarantine"]]
    view["empty_rows"] = [_row_view(row) for row in view["empty_rows"]]
    view["rows"] = [_row_view(row) for row in view["rows"]]
    view["partitions"] = [
        {**part, "label": part["partition"]} for part in view["partitions"]
    ]
    view["assignment_count"] = len(view["assignments"])
    view["quarantine_count"] = len(view["quarantine"])
    view["input_count"] = len(view["rows"])
    view["empty_count"] = len(view["empty_rows"])
    view["balanced"] = (
        view["input_count"]
        == view["assignment_count"] + view["quarantine_count"] + view["empty_count"]
    )
    view["replay_notice"] = view.get("last_replay")
    return view


def _row_view(row: dict) -> dict:
    return {
        "row_id": row.get("row_id"),
        "source_line": row.get("source_line"),
        "raw_key": row.get("raw_key"),
        "raw_value": row.get("raw_value"),
        "source": row.get("source"),
        "status": row.get("status"),
        "reason": row.get("reason"),
        "reason_code": row.get("reason_code", row.get("reason")),
        "reason_text": row.get("reason_text"),
        "target": row.get("target"),
        "normalized_value": row.get("normalized_value"),
        "partition": row.get("partition"),
        "partitions": row.get("partitions", {}),
        "kind": row.get("kind"),
        "object_found": row.get("object_found"),
        "attempts": row.get("attempts", 0),
        "last_error": row.get("last_error"),
        "size": row.get("size"),
        "etag": row.get("etag"),
    }
