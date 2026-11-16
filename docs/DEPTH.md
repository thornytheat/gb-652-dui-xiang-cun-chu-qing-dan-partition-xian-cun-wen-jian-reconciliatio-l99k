# 深度登记 对象存储清单与分区现存文件对账

        - 规模：中型
        - 核心规则：路径规范化后按 key、size 与摘要逐项比对，目录标记、空分区和缺失对象不得混为一类。
        - 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

        ## 功能模块

        | 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
        |---|---|---|---|---|
        | M1 清单采信 | 运行样例并复核证据 | `/api/manifests/import` / `/manifest` | `app/modules/m1_manifest/core.py` `app/modules/m1_manifest/service.py` | `tests/test_m1_manifest.py` |
| M2 路径契约 | 运行样例并复核证据 | `/api/paths/normalize` / `/paths` | `app/modules/m2_paths/core.py` `app/paths.py` | `tests/test_m2_paths.py` `tests/test_m2_sources.py` |
| M3 对象核验 | 运行样例并复核证据 | `/api/objects/verify` / `/objects` | `app/modules/m3_objects/core.py` `app/modules/m3_objects/service.py` | `tests/test_m3_objects.py` |
| M4 分区判定 | 导入清单、核对归属与隔离原因、修正映射后重放 | `/api/partitions` `/api/partitions/import` `/api/partitions/{batch_id}/replay` / `/partitions` | `app/modules/m4_partitions/core.py` `app/modules/m4_partitions/service.py` `app/paths.py` | `tests/test_m4_partitions.py` `tests/test_m4_partition_import.py` |
| M5 差异对账 | 运行样例并复核证据 | `/api/reconcile` / `/reconcile` | `app/modules/m5_reconcile/core.py` `app/modules/m5_reconcile/service.py` | `tests/test_m5_reconcile.py` |
| M6 批次导出 | 运行样例并复核证据 | `/api/export/differences.csv` / `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` |

        ## 陷阱登记

        | TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
        |---|---|---|---|---|---|
        | TRAP-02 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_manifest/core.py` | `tests/test_m1_manifest.py::test_trap_1` |
| TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_paths/core.py` | `tests/test_m2_paths.py::test_trap_1` |
| TRAP-08 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_objects/core.py` | `tests/test_m3_objects.py::test_trap_1` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_partitions/core.py` | `tests/test_m4_partitions.py::test_trap_1` |
| TRAP-14 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_reconcile/core.py` | `tests/test_m5_reconcile.py::test_trap_1` |
| TRAP-16 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-18 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_manifest/core.py` | `tests/test_m1_manifest.py::test_trap_2` |
| TRAP-21 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_paths/core.py` | `tests/test_m2_paths.py::test_trap_2` |
| TRAP-22 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_objects/core.py` | `tests/test_m3_objects.py::test_trap_2` |
| TRAP-23 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_partitions/core.py` | `tests/test_m4_partitions.py::test_trap_2` |
| TRAP-24 | 供货方清单的 `%2F`、`\` 与 `//` 与原生文件名混淆 | 统一解码会把 `a%2Fb` 与 `a/b` 串成同一对象；全部原样又会把 `a%20b` 误报缺失 | 按清单来源选择解释方式，现存对象始终字面解释 | `app/paths.py` `app/modules/m4_partitions/core.py` | `tests/test_m4_partition_import.py::test_native_source_keeps_literal_names` `tests/test_m4_partition_import.py::test_source_mismatch_does_not_merge_objects` |
| TRAP-25 | 多行清单映射到同一目标 | 后者覆盖前者，静默丢行 | 冲突组全部隔离，不挑选覆盖 | `app/modules/m4_partitions/core.py` | `tests/test_m4_partition_import.py::test_conflicting_mapping_quarantines_whole_group` |
| TRAP-26 | 修正映射后重放隔离行 | 全量重跑导致已通过行重复计入分区 | 只重放隔离行，分区按通过行去重集合重算，失败可安全重试 | `app/modules/m4_partitions/core.py` `app/modules/m4_partitions/service.py` | `tests/test_m4_partition_import.py::test_replay_only_quarantined_rows` `tests/test_m4_partition_import.py::test_replay_failure_does_not_duplicate_count` |

        ## 对账规则

        - 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
        - 核心数值或事件数在转换、判定和导出之间保持守恒。
        - 同一输入摘要重跑复用原运行编号，不增加结果行。
        - 分区归属按通过行的去重目标键计算；重放只处理隔离行。

        ## 已知不覆盖

        - 生产系统连接、身份认证和外部密钥不在演示范围内。
