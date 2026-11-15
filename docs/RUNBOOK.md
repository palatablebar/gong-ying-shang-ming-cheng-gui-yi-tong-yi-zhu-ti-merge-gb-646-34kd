# 运行手册 供应商主数据台账

## 启动

```bash
docker compose up -d --build
curl -s http://127.0.0.1:18205/api/health
```

首次打开任意页面会按内置样例自动采信一次，随后逐级计算归一名、分块、候选对、合并簇与金账。

## 重建数据库

```bash
docker compose down -v
docker compose up -d --build
```

`-v` 会删除命名卷 `merge-data`，批次表、复核决定与金账表一并清空。

## 重跑与幂等

- `POST /api/suppliers/import` 带 `force: true` 时按同一批文件重算。
- 批次号由输入摘要（按文件名排序的 sha256 拼接）与规则版本共同决定，同一批文件得到同一个 `run_id`；
  命中已有批次时直接复用结果，不新增记录。
- 复核决定写入后，簇、金账、字段溯源、金额账本与待核对会立即重算，不需要手工触发。
- 金账裁决只依赖 `(可信级别, last_update, 供应商号)`，与文件处理顺序无关，
  同批重跑金账四张表逐字一致、出账合计不变。

## 常用请求

```bash
curl -s -X POST http://127.0.0.1:18205/api/suppliers/import -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:18205/api/golden
curl -s http://127.0.0.1:18205/api/reconcile
curl -s http://127.0.0.1:18205/api/export/master.csv -o master.csv
curl -s http://127.0.0.1:18205/api/export/golden.csv -o golden.csv
curl -s http://127.0.0.1:18205/api/export/provenance.csv -o provenance.csv
curl -s http://127.0.0.1:18205/api/export/amount-ledger.csv -o amount-ledger.csv
curl -s http://127.0.0.1:18205/api/export/manifest.json -o manifest.json
```

判定一张复核单：

```bash
curl -s -X POST http://127.0.0.1:18205/api/reviews/P-00105/decision \
  -H 'Content-Type: application/json' \
  -d '{"decision":"merge","expected_version":0}'
```

## 月底出账核对顺序

1. `POST /api/suppliers/import` 重新采信，确认记录守恒（输入 = 采纳 + 隔离 + 字段接管）。
2. `POST /api/golden` 装配金账，核对 `amount_observed = amount_payable + amount_suppressed`。
3. 打开 `/golden` 处理待核对：`G_TAX_PENDING` 核实主体身份，`G_FIELD_CONFLICT` 人工选值。
4. 只对 `payment_blocked = 0` 的主体出账；阻断主体的账户或税号未核实前不得付款。
5. 用 `amount-ledger.csv` 核对每个主体只有一条 `adopted` 余额，其余均为 `suppressed`。
6. 重跑一次同一批文件，确认 `run_id`、出账合计与金账明细完全一致后再放行付款。

## 失败码处理

| 原因码 | 处理 |
|---|---|
| `E_FILE_MISSING` | 检查 `sample_data/` 下的文件名与大小写 |
| `E_MISSING_COLUMN` | 表头缺必填列，按列契约补列后重试 |
| `E_DECODE_FAILED` | 文件既非 UTF-8 也非 GB18030，需转码 |
| `Q_ROW_SHORT` / `Q_ROW_LONG` | 该行进隔离区，修正分隔符后重跑 |
| `G_TAX_PENDING` | 簇内多个可信税号，核实主体前阻断付款 |
| `G_ACCOUNT_UNVERIFIED` | 账户来自非主档来源或缺失，补主档账号后重跑 |
| `G_FIELD_CONFLICT` | 同可信级别多值无法自动裁决，人工在金账页核对 |
| `G_AMOUNT_SUPPRESSED` | 正常抑制：重复余额快照，不进合计，可在金额账本追溯 |
| `V_STALE_DECISION` | 页面停留过久，刷新后按新版本再提交 |
| `V_BAD_DECISION` | 税号冲突的候选对不允许合并 |
| `V_UNKNOWN_PAIR` | 候选对编号不属于当前批次，刷新页面 |

## 测试

```bash
docker compose exec -T app python -m pytest -q
```
