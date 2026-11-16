# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `batch_id` | string | 否 | 分区清单批次的稳定编号，由输入摘要派生 |
| `source` | string | 否 | 清单来源，决定路径解释方式：`supplier` / `native` |
| `source_line` | integer | 否 | 原文件物理行号 |
| `row_id` | string | 否 | 批次内稳定的行编号，供重放定位 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_key` | string | 是 | 清单原键，逐字保留，不参与归一 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按来源规则解释后的目标键 |
| `partition` | string | 是 | 目标键所属分区；根目录记作 `(root)` |
| `partitions` | object | 是 | 目录段中的分区键值对，按原顺序 |
| `kind` | string | 是 | `object` / `marker`（`_EMPTY`、`_SUCCESS`）/ `directory` |
| `attempts` | integer | 否 | 该行参与重放的次数 |
| `rule_version` | string | 否 | 计算规则版本 |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。

## 清单来源与路径解释

| 来源 | 百分号转义 | 反斜杠 | 连续斜杠 |
|---|---|---|---|
| `supplier` | 逐段解码 | 路径分隔符 | 折叠 |
| `native` | 字面字符 | 字面字符 | 字面字符 |

现存对象清单始终按 `native` 字面解释。同一目标键出现多条不同原键或
不同元数据时，相关行全部记 `CONFLICTING_MAPPING`，不保留其中一条。

## 批次守恒

`input_count = valid_count + quarantine_count + empty_count`。分区归属
按通过行的去重目标键计算，重放隔离行只追加通过行，不重复计入分区。
