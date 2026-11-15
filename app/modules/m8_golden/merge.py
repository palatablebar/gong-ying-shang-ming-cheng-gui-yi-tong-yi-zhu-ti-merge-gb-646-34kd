"""同供应商号多行的字段级合并：整行不再先到先得。

旧规则下后到的正确值被整行当冲突丢弃；这里按字段可信级别裁决，
晚到的更正若来自更可信（或同级且更新时间更新）的来源，就只接管对应字段，
其余字段保持原值，每个被改动/被驳回的字段都留下一条证据。
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from ...reasons import (
    G_AMOUNT_ADOPTED,
    G_FIELD_CONFLICT,
    G_FIELD_OVERRIDDEN,
)
from . import trust

# 同号行之间允许按来源可信度裁决的字段。
_MERGE_FIELDS = trust.MERGE_FIELDS


def _newer_or_equal(incoming: dict, base: dict) -> bool:
    """同级时晚到的更正接管：更新时间更新者胜；日期相同则维持先到者。"""
    incoming_date = (incoming.get("last_update") or "").strip()
    base_date = (base.get("last_update") or "").strip()
    if not incoming_date:
        return False
    if not base_date:
        return True
    return tuple(int(p) for p in incoming_date.split("-")) >= \
        tuple(int(p) for p in base_date.split("-"))


def _field_wins(incoming: dict, base: dict, field: str) -> bool:
    """incoming 的该字段是否应接管 base 的同名字段。"""
    incoming_level = trust.trust_level(incoming["erp"], field)
    base_level = trust.trust_level(base["erp"], field)
    if incoming_level > base_level:
        return True
    if incoming_level < base_level:
        return False
    return _newer_or_equal(incoming, base)


def merge_same_id(base: dict, incoming: dict) -> tuple:
    """把 incoming 合进 base 的字段级副本。

    返回 (merged_record, actions)；actions 为每个发生分歧的字段留证据：
    {"field", "reason_code", "kept", "rejected", "source_file", "line_no"}。
    """
    merged = deepcopy(base)
    actions = []

    for field in _MERGE_FIELDS:
        new_value = str(incoming.get(field, "")).strip()
        if not new_value or new_value == str(merged.get(field, "")).strip():
            continue
        if not str(merged.get(field, "")).strip():
            # 原值为空：直接补入，仍记录接管来源。
            merged[field] = incoming[field]
            actions.append(_action(field, G_FIELD_OVERRIDDEN, incoming, merged, incoming))
            continue
        if _field_wins(incoming, merged, field):
            merged[field] = incoming[field]
            actions.append(_action(field, G_FIELD_OVERRIDDEN, incoming, merged, incoming))
        else:
            # 两个可信来源各执一词且新来源不够新：不静默覆盖，转待核对。
            actions.append(_action(field, G_FIELD_CONFLICT, incoming, merged, incoming))

    # 金额：同号行按权威级别选一条，不重复计入；选输的余额留证据。
    chosen, _candidates = trust.choose_amount([merged, incoming])
    chosen_record = chosen[0]
    if chosen_record is incoming and Decimal(str(incoming["ytd_amount"])) != \
            Decimal(str(merged["ytd_amount"])):
        actions.append({
            "field": "ytd_amount",
            "reason_code": G_AMOUNT_ADOPTED,
            "kept": f"{incoming['source_file']}:{incoming['line_no']}",
            "rejected": f"{merged['source_file']}:{merged['line_no']}",
            "source_file": incoming["source_file"],
            "line_no": incoming["line_no"],
        })
    merged["ytd_amount"] = str(chosen_record["ytd_amount"])
    return merged, actions


def _action(field: str, reason_code: str, incoming: dict, merged: dict, record: dict) -> dict:
    return {
        "field": field,
        "reason_code": reason_code,
        "kept": f"{record['source_file']}:{record['line_no']}",
        "rejected": f"{merged['source_file']}:{merged['line_no']}",
        "source_file": incoming["source_file"],
        "line_no": incoming["line_no"],
    }
