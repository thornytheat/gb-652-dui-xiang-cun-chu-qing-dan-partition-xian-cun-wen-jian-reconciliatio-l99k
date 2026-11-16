REASONS = {
    "OK": "通过",
    "BAD_COLUMNS": "字段数量不符", "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突", "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义", "STALE_VERSION": "提交版本已过期",
    # M4 分区清单
    "CONFLICTING_MAPPING": "同一目标映射冲突",
    "DUPLICATE_ROW": "重复清单行，已保留最早一条",
    "CONFLICTING_PARTITION": "同一路径分区字段取值冲突",
    "INVALID_ESCAPE": "非法百分号转义",
    "EMPTY_KEY": "路径为空",
    "EMPTY_ROW": "空记录",
    "UNKNOWN_SOURCE": "清单来源未登记",
    "MISSING": "清单内对象在现存对象中缺失",
    "GAP": "期望分区缺失",
}

def reason(code: str) -> str:
    return REASONS.get(code, "未登记原因")
