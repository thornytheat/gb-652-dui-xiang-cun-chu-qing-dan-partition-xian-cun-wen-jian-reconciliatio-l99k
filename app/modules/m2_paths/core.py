"""路径契约：按清单来源解释后统一分区顺序。"""
from app.errors import DomainError
from app.paths import canonical_source, kind_of, split_key
from app.paths import partition_values as _partition_values

SAMPLE = {
    "paths": [
        "/lake//dt=2026-11-15/hour=01/a%20b.parquet",
        "lake/hour=01/dt=2026-11-15/000123.parquet",
        "lake/_SUCCESS",
    ]
}


def normalize(path, source: str = "supplier"):
    """解释单个路径并按固定分区顺序重排。

    解释方式由清单来源决定，默认 ``supplier`` 保持原有转义写法行为；
    ``native`` 下百分号、连续斜杠和反斜杠都是字面文件名。
    """
    interpreted = split_key(path, source)
    segments = interpreted["segments"]
    fixed = []
    partitions = []
    for segment in segments[:-1]:
        (partitions if "=" in segment else fixed).append(segment)
    filename = segments[-1:] if segments else []
    ordered = fixed + sorted(partitions) + filename
    return "/".join(ordered)


def partition_values(path, source: str = "supplier"):
    interpreted = split_key(path, source)
    return _partition_values(interpreted["segments"], interpreted["is_dir"])


def describe(path, source: str = "supplier"):
    interpreted = split_key(path, source)
    normalized = normalize(path, source)
    return {
        "raw_key": path,
        "source": interpreted["source"],
        "target": interpreted["target"],
        "normalized": normalized,
        "segments": interpreted["segments"],
        "partitions": _partition_values(interpreted["segments"], interpreted["is_dir"]),
        "kind": kind_of(interpreted["target"]),
    }


def run(payload):
    d = SAMPLE | payload
    default_source = d.get("source", "supplier")
    try:
        canonical_source(default_source)
    except DomainError as exc:
        raise DomainError(exc.code, exc.message) from exc
    paths = d["paths"]
    rows = []
    for item in paths:
        if isinstance(item, dict):
            raw = item.get("key", item.get("raw_key"))
            row_source = item.get("source", default_source)
        else:
            raw, row_source = item, default_source
        described = describe(raw, row_source)
        described["normalized"] = normalize(raw, row_source)
        rows.append(described)
    return {
        "status": "READY",
        "source": canonical_source(default_source),
        "input_count": len(paths),
        "valid_count": len(rows),
        "quarantine_count": 0,
        "paths": [row["normalized"] for row in rows],
        "rows": rows,
        "partitions": rows[0]["partitions"] if rows else {},
        "reason": "OK",
    }
