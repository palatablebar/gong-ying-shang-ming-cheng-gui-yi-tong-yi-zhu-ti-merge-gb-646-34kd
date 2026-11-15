"""字段级可信来源裁决：每个来源只贡献自己可信的字段。

裁决不依赖输入文件顺序，只依赖 (可信级别, 更新时间, 供应商号)，
因此同一批文件重跑必然得到同一份金账。
"""
from __future__ import annotations

from decimal import Decimal

from ...config import (
    AMOUNT_AUTHORITY,
    BANK_ACCOUNT_TRUSTED_SOURCES,
    FIELD_TRUST,
    GOLDEN_FIELDS,
)

# 采信层同号合并时允许被晚到更正接管的字段。
MERGE_FIELDS = ("supplier_no", "raw_name", "tax_id", "city", "bank_account")

CENT = Decimal("0.01")


def trust_level(erp: str, field: str) -> int:
    return FIELD_TRUST.get(erp, {}).get(field, 0)


def amount_authority(erp: str) -> int:
    return AMOUNT_AUTHORITY.get(erp, 0)


def _update_key(record: dict) -> tuple:
    """last_update 降序：越新越大；空日期排最后。"""
    raw = (record.get("last_update") or "").strip()
    parts = tuple(int(part) for part in raw.split("-")) if raw else (0,)
    return tuple(-part for part in parts)


def field_candidates(records: list, field: str) -> list:
    """返回对该字段有非空值且来源可信的候选，按裁决键排序。"""
    candidates = [
        record for record in records
        if str(record.get(field, "")).strip() and trust_level(record["erp"], field) > 0
    ]
    return sorted(
        candidates,
        key=lambda record: (
            -trust_level(record["erp"], field),
            _update_key(record),
            record["supplier_id"],
        ),
    )


def pick_field(records: list, field: str) -> dict | None:
    """选一条胜出记录；调用方据此写金账与溯源。"""
    candidates = field_candidates(records, field)
    return candidates[0] if candidates else None


def distinct_field_values(records: list, field: str, *, trusted_only: bool = True) -> dict:
    """收集字段的不同非空值 -> 提供该值的记录列表。"""
    values: dict = {}
    for record in records:
        value = str(record.get(field, "")).strip()
        if not value:
            continue
        if trusted_only and trust_level(record["erp"], field) == 0:
            continue
        values.setdefault(value, []).append(record)
    return values


def is_account_trusted(erp: str) -> bool:
    return erp in BANK_ACCOUNT_TRUSTED_SOURCES


def trusted_account(records: list) -> dict | None:
    """账户只来自主档类来源；金额来源带的账号永不进付款主数据。"""
    candidates = [
        record for record in records
        if str(record.get("bank_account", "")).strip() and is_account_trusted(record["erp"])
    ]
    candidates.sort(key=lambda record: (
        -trust_level(record["erp"], "bank_account"),
        _update_key(record),
        record["supplier_id"],
    ))
    return candidates[0] if candidates else None


def unverified_accounts(records: list) -> list:
    """来源不可信却带了账号：只登记为待核对，不进金账。"""
    return [
        record for record in records
        if str(record.get("bank_account", "")).strip() and not is_account_trusted(record["erp"])
    ]


def choose_amount(records: list) -> tuple:
    """本年累计去重选一条。

    返回 (胜出记录, 全部候选)。规则：
    1. 只在正数余额里选权威级别最高的（0.00/哨兵不覆盖真实余额）；
    2. 没有正数时回落到 0.00 候选，仍按权威级别排序；
    3. 同级按 last_update 最新、再按供应商号兜底。
    每个主体最终只有一条余额进入合计，跨系统快照绝不相加。
    """
    candidates = []
    for record in records:
        amount = Decimal(str(record.get("ytd_amount", "0"))).quantize(CENT)
        candidates.append((record, amount, amount_authority(record["erp"])))
    positive = [item for item in candidates if item[1] > 0]
    pool = positive if positive else candidates

    def sort_key(item):
        record, _amount, authority = item
        return (-authority, _update_key(record), record["supplier_id"])

    pool = sorted(pool, key=sort_key)
    return pool[0], sorted(candidates, key=sort_key)


def golden_field_names() -> tuple:
    return GOLDEN_FIELDS
