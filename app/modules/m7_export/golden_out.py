"""金账 CSV：字段级采信结果、逐字段溯源与去重后的金额账本。"""
from __future__ import annotations

import csv
import io
import json
from decimal import Decimal

from ...config import GOLDEN_FIELD_LABELS


def render_golden(golden, provenance, ledger) -> str:
    """一个主体一行：每个字段的值与采信来源并列，付款状态显式标出。"""
    prov_index = {}
    for row in provenance:
        prov_index[(row["cluster_id"], row["field"])] = row
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow((
        "主体编号", "代表供应商号", "名称", "名称来源", "税号", "税号来源",
        "城市", "城市来源", "开户行账号", "账户来源", "账户核实",
        "本年累计(元)", "余额来源", "付款状态", "阻断原因",
    ))
    for row in golden:
        name_p = prov_index.get((row["cluster_id"], "raw_name"), {})
        tax_p = prov_index.get((row["cluster_id"], "tax_id"), {})
        city_p = prov_index.get((row["cluster_id"], "city"), {})
        acct_p = prov_index.get((row["cluster_id"], "bank_account"), {})
        block = "、".join(json.loads(row["block_reasons"] or "[]"))
        writer.writerow((
            row["cluster_id"], row["rep_id"],
            row["raw_name"], _source(name_p),
            row["tax_id"], _source(tax_p),
            row["city"], _source(city_p),
            row["bank_account"], _source(acct_p),
            "已核实" if row["account_verified"] else "未核实",
            f"{Decimal(row['ytd_amount']):.2f}",
            f"{row['amount_erp']} {row['amount_supplier_id']}",
            "阻断" if row["payment_blocked"] else "可付款",
            block,
        ))
    return buffer.getvalue()


def render_provenance(provenance) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("主体编号", "字段", "字段中文名", "采信值", "采信供应商号", "来源ERP",
                     "来源文件", "来源行号", "裁决原因码", "候选值个数"))
    for row in provenance:
        writer.writerow((
            row["cluster_id"], row["field"],
            GOLDEN_FIELD_LABELS.get(row["field"], row["field"]),
            row["chosen_value"], row["chosen_supplier_id"], row["chosen_erp"],
            row["chosen_source_file"], row["chosen_line_no"],
            row["reason_code"], row["candidate_count"],
        ))
    return buffer.getvalue()


def render_amount_ledger(ledger) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("主体编号", "供应商号", "来源ERP", "来源文件", "行号",
                     "观察余额(元)", "权威级别", "裁决", "原因码", "更新时间"))
    for row in ledger:
        writer.writerow((
            row["cluster_id"], row["supplier_id"], row["erp"], row["source_file"],
            row["line_no"], f"{Decimal(row['observed_amount']):.2f}",
            row["authority"],
            "采信" if row["decision"] == "adopted" else "抑制不计",
            row["reason_code"], row["last_update"],
        ))
    return buffer.getvalue()


def render_pending(pending) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("主体编号", "供应商号", "字段", "观察值", "来源ERP",
                     "来源文件", "行号", "原因码", "说明"))
    for row in pending:
        writer.writerow((
            row["cluster_id"], row["supplier_id"],
            GOLDEN_FIELD_LABELS.get(row["field"], row["field"]),
            row["observed_value"], row["erp"], row["source_file"], row["line_no"],
            row["reason_code"], row["detail"],
        ))
    return buffer.getvalue()


def _source(prov_row: dict) -> str:
    if not prov_row:
        return ""
    return (f"{prov_row.get('chosen_erp', '')} {prov_row.get('chosen_supplier_id', '')} "
            f"{prov_row.get('chosen_source_file', '')}:{prov_row.get('chosen_line_no', 0)}").strip()
