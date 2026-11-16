# 对象存储清单与分区现存文件对账

面向一线复核人员的单服务数据工作台。样例数据随仓库提供，结论保留行号、原因码和原值。

## 业务模块

- M1 清单采信：`/api/manifests/import`
- M2 路径契约：`/api/paths/normalize`
- M3 对象核验：`/api/objects/verify`
- M4 分区判定：`/api/partitions`、`/api/partitions/import`、`/api/partitions/{batch_id}/replay`
- M5 差异对账：`/api/reconcile`
- M6 批次导出：`/api/export/differences.csv`

## 核心规则

路径规范化后按 key、size 与摘要逐项比对，目录标记、空分区和缺失对象不得混为一类。

分区页面（`/partitions`）可导入清单、核对归属与隔离原因。清单路径按
**清单来源**解释，而不是统一解码：

| 来源 | 百分号转义 | 反斜杠 | 连续斜杠 |
|---|---|---|---|
| `supplier`（供货方） | 逐段解码 | 路径分隔符 | 折叠 |
| `native`（原生） | 字面字符 | 字面字符 | 字面字符 |

现存对象清单始终按字面解释。这样供货方的 `a%2Fb` 能落到真实对象
`a/b`，而原生清单里的 `a%2Fb` 仍是另一个对象，不会因为统一解码串对象，
也不会因为全部原样而误报缺失。

同一目标键出现多条不同原键或不同元数据时，相关行全部隔离并记
`CONFLICTING_MAPPING`，不挑选一条覆盖。修正映射后只重放隔离行，已通过
行保持不变；重放失败可安全重试，分区归属按通过行的去重集合计算，不会
重复计数。

## 启动

```bash
docker compose up --build
```

浏览器访问 `http://localhost:18211`。运行测试：

```bash
docker compose exec -T app pytest -q
```

## 样例流程

依次运行六个模块；最后在批次页复核输入、有效、隔离与输出合计。同一输入摘要重复执行时复用原 `run_id`。

分区页面支持直接粘贴清单或将 `sample_data/manifest_dirty.csv` 作为文件
导入，同一输入摘要重复导入会复用同一 `batch_id`，不新增分区结果。

## 已知不覆盖

不处理加密文件与需要外部密钥的对象；不连接真实生产存储。
