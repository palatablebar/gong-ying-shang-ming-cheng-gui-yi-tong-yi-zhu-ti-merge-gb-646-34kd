"""M8 金账装配：簇级字段级采信、余额去重、账户核验、待核对与逐字段溯源。

输入是 M5 的合并簇（同一主体的所有来源行已在一个簇内）。
本模块不做实体识别，只回答"每个字段采用谁、余额采哪条、哪些值不能付款"，
全部裁决只依赖来源可信画像与更新时间，重跑结果逐分一致。
"""
from __future__ import annotations

from decimal import Decimal

from ... import store
from ...errors import NotFound
from ...reasons import (
    G_ACCOUNT_UNVERIFIED,
    G_AMOUNT_ADOPTED,
    G_AMOUNT_SUPPRESSED,
    G_FIELD_CONFLICT,
    G_FIELD_OVERRIDDEN,
    G_FIELD_PICKED,
    G_TAX_PENDING,
    V_UNKNOWN_RUN,
)
from ..m2_normalize import service as normalize_service
from ..m5_clusters import service as cluster_service
from . import trust

CENT = Decimal("0.01")


def ensure_golden(run_id: str) -> dict:
    """总是重算：复核决定改了簇之后，金账与合计立即一致。"""
    normalize_service.ensure_normalized(run_id)
    cluster_service.ensure_clusters(run_id)
    records = {row["supplier_id"]: row for row in store.list_records(run_id)}
    if not records:
        raise NotFound(V_UNKNOWN_RUN, run_id)
    clusters = store.list_clusters(run_id)
    members = store.list_cluster_members(run_id)

    by_cluster: dict = {}
    for member in members:
        by_cluster.setdefault(member["cluster_id"], []).append(member["supplier_id"])

    golden, provenance, ledger_rows, pending = [], [], [], []

    for cluster in clusters:
        supplier_ids = sorted(by_cluster.get(cluster["cluster_id"], []))
        rows = [records[supplier_id] for supplier_id in supplier_ids]
        context = _assemble_cluster(cluster, rows)
        golden.append(context["golden"])
        provenance.extend(context["provenance"])
        ledger_rows.extend(context["ledger"])
        pending.extend(context["pending"])

    store.save_golden(run_id, golden, provenance, ledger_rows, pending)
    return summary(run_id)


def _assemble_cluster(cluster: dict, rows: list) -> dict:
    cluster_id = cluster["cluster_id"]
    provenance, pending, ledger = [], [], []

    # ---- 1. 身份与描述字段：每个字段只在可信来源里选一条 ----
    picked = {}
    generic_fields = tuple(name for name in trust.golden_field_names()
                           if name != "bank_account")
    for field in generic_fields:
        winner = trust.pick_field(rows, field)
        distinct = trust.distinct_field_values(rows, field)
        if winner is None:
            provenance.append(_provenance(cluster_id, field, "", None, G_FIELD_PICKED, len(distinct)))
            continue
        reason = G_FIELD_PICKED
        if len(distinct) > 1:
            # 多值并存：胜出值按可信级别接管。跨级别的覆盖只记溯源（晚到更正），
            # 同一可信级别内各执一词、无法自动裁决的才转人工待核对。
            # supplier_no 是各系统内部编码，天然不同，不产生待核对；
            # raw_name 的写法差异若归一名相同，视为同一主体的别名而非冲突。
            winner_level = trust.trust_level(winner["erp"], field)
            same_tier_values = [
                (value, providers) for value, providers in distinct.items()
                if any(trust.trust_level(provider["erp"], field) == winner_level
                       for provider in providers)
            ]
            equivalent = field == "raw_name" and _names_equivalent(distinct, rows)
            if field != "supplier_no" and not equivalent and len(same_tier_values) > 1:
                reason = G_FIELD_CONFLICT
                for value, providers in distinct.items():
                    if value == str(winner[field]).strip():
                        continue
                    for provider in providers:
                        pending.append(_pending(cluster_id, provider, field, G_FIELD_CONFLICT,
                                                value,
                                                f"同可信级别多值，金账暂采 {winner['supplier_id']}"))
            else:
                reason = G_FIELD_OVERRIDDEN
        picked[field] = winner
        provenance.append(_provenance(cluster_id, field, str(winner[field]).strip(),
                                      winner, reason, len(distinct)))

    # ---- 2. 税号硬核实：多个可信税号 = 主体身份未核实，禁止付款 ----
    tax_values = trust.distinct_field_values(rows, "tax_id")
    tax_pending = len(tax_values) > 1
    if tax_pending:
        for value, providers in sorted(tax_values.items()):
            for provider in providers:
                pending.append(_pending(cluster_id, provider, "tax_id", G_TAX_PENDING,
                                        value, f"簇内 {len(tax_values)} 个税号待核实"))
    tax_winner = picked.get("tax_id")
    tax_id = str(tax_winner["tax_id"]).strip() if tax_winner else ""

    # ---- 3. 银行账户：只接受主档类来源，金额来源的账号永不入金账 ----
    account_winner = trust.trusted_account(rows)
    for record in trust.unverified_accounts(rows):
        pending.append(_pending(cluster_id, record, "bank_account", G_ACCOUNT_UNVERIFIED,
                                record["bank_account"],
                                f"来源 {record['erp']} 非账户可信来源，禁止带入付款"))
    bank_account = str(account_winner["bank_account"]).strip() if account_winner else ""
    account_candidates = {r["bank_account"] for r in rows
                          if str(r.get("bank_account", "")).strip()}
    account_reason = G_FIELD_PICKED if account_winner else G_ACCOUNT_UNVERIFIED
    provenance.append(_provenance(cluster_id, "bank_account", bank_account,
                                  account_winner, account_reason, len(account_candidates)))
    account_verified = bool(account_winner)

    # ---- 4. 本年累计：每个主体只采一条权威余额，其余快照抑制不重复计入 ----
    chosen, candidates = trust.choose_amount(rows)
    chosen_record, chosen_amount, _authority = chosen
    for record, amount, authority in candidates:
        adopted = record["supplier_id"] == chosen_record["supplier_id"]
        ledger.append({
            "cluster_id": cluster_id,
            "supplier_id": record["supplier_id"],
            "erp": record["erp"],
            "source_file": record["source_file"],
            "line_no": record["line_no"],
            "observed_amount": amount.quantize(CENT),
            "authority": authority,
            "decision": "adopted" if adopted else "suppressed",
            "reason_code": G_AMOUNT_ADOPTED if adopted else G_AMOUNT_SUPPRESSED,
            "last_update": record.get("last_update", ""),
        })
    provenance.append(_provenance(
        cluster_id, "ytd_amount", str(chosen_amount.quantize(CENT)),
        chosen_record, G_AMOUNT_ADOPTED, len(candidates)))

    # ---- 5. 付款阻断：身份未核实或账户未核实都不能出账 ----
    block_reasons = []
    if tax_pending:
        block_reasons.append(G_TAX_PENDING)
    if not account_verified:
        block_reasons.append(G_ACCOUNT_UNVERIFIED)

    name_winner = picked.get("raw_name")
    no_winner = picked.get("supplier_no")
    city_winner = picked.get("city")
    golden = {
        "cluster_id": cluster_id,
        "rep_id": cluster["rep_id"],
        "supplier_no": str(no_winner["supplier_no"]).strip() if no_winner else "",
        "raw_name": str(name_winner["raw_name"]).strip() if name_winner else "",
        "tax_id": tax_id,
        "city": str(city_winner["city"]).strip() if city_winner else "",
        "bank_account": bank_account,
        "ytd_amount": chosen_amount.quantize(CENT),
        "amount_supplier_id": chosen_record["supplier_id"],
        "amount_erp": chosen_record["erp"],
        "account_verified": account_verified,
        "payment_blocked": bool(block_reasons),
        "block_reasons": block_reasons,
    }
    return {"golden": golden, "provenance": provenance, "ledger": ledger, "pending": pending}


def _names_equivalent(distinct: dict, rows: list) -> bool:
    """多个原始名称写法，若归一名一致，就是同一主体的别名，不算待核对。"""
    norm_by_id = {row["supplier_id"]: (row.get("norm_name") or "").strip() for row in rows}
    normalized = set()
    for _value, providers in distinct.items():
        for provider in providers:
            norm = norm_by_id.get(provider["supplier_id"], "")
            if norm:
                normalized.add(norm)
    return len(normalized) == 1


def _provenance(cluster_id, field, value, winner, reason_code, candidate_count) -> dict:
    return {
        "cluster_id": cluster_id,
        "field": field,
        "chosen_value": value,
        "chosen_supplier_id": winner["supplier_id"] if winner else "",
        "chosen_erp": winner["erp"] if winner else "",
        "chosen_source_file": winner["source_file"] if winner else "",
        "chosen_line_no": winner["line_no"] if winner else 0,
        "reason_code": reason_code,
        "candidate_count": max(candidate_count, 1),
    }


def _pending(cluster_id, record, field, reason_code, value, detail) -> dict:
    return {
        "cluster_id": cluster_id,
        "supplier_id": record["supplier_id"],
        "field": field,
        "reason_code": reason_code,
        "observed_value": value,
        "erp": record["erp"],
        "source_file": record["source_file"],
        "line_no": record["line_no"],
        "detail": detail,
    }


def summary(run_id: str) -> dict:
    golden = store.list_golden(run_id)
    ledger = store.list_amount_ledger(run_id)
    pending = store.list_golden_pending(run_id)
    adopted = sum(Decimal(row["observed_amount"]) for row in ledger
                  if row["decision"] == "adopted")
    observed = sum(Decimal(row["observed_amount"]) for row in ledger)
    suppressed = observed - adopted
    blocked = sum(1 for row in golden if row["payment_blocked"])
    return {
        "run_id": run_id,
        "subjects": len(golden),
        "payment_blocked": blocked,
        "payment_ready": len(golden) - blocked,
        "amount_payable": str(adopted.quantize(CENT)),
        "amount_suppressed": str(suppressed.quantize(CENT)),
        "amount_observed": str(observed.quantize(CENT)),
        "suppressed_rows": sum(1 for row in ledger if row["decision"] == "suppressed"),
        "pending_items": len(pending),
        "pending_codes": sorted({row["reason_code"] for row in pending}),
    }
