"""M8 金账装配：字段级可信来源、余额去重、账户核验、待核对与重跑确定性。"""
from __future__ import annotations

from decimal import Decimal

from app import store
from app.config import FIELD_TRUST
from app.modules.m8_golden import merge, service, trust
from tests.fixtures import expected as EXP


def _golden_index(run_id: str) -> dict:
    return {row["cluster_id"]: row for row in store.list_golden(run_id)}


def _prov_index(run_id: str) -> dict:
    return {(row["cluster_id"], row["field"]): row
            for row in store.list_provenance(run_id)}


def test_golden_subjects_and_payable_totals(run):
    summary = service.ensure_golden(run["run_id"])
    assert summary["subjects"] == EXP.GOLDEN_SUBJECTS
    assert Decimal(summary["amount_payable"]) == EXP.GOLDEN_PAYABLE
    assert Decimal(summary["amount_suppressed"]) == EXP.GOLDEN_SUPPRESSED
    assert Decimal(summary["amount_observed"]) == EXP.AMOUNT_TOTAL
    # 逐分守恒：观察口径 = 出账口径 + 抑制口径。
    assert Decimal(summary["amount_payable"]) + Decimal(summary["amount_suppressed"]) \
        == Decimal(summary["amount_observed"])


def test_each_subject_adopts_one_amount_only(run):
    service.ensure_golden(run["run_id"])
    ledger = store.list_amount_ledger(run["run_id"])
    adopted = [row for row in ledger if row["decision"] == "adopted"]
    suppressed = [row for row in ledger if row["decision"] == "suppressed"]
    assert len(adopted) == EXP.GOLDEN_SUBJECTS
    assert len(suppressed) == EXP.GOLDEN_SUPPRESSED_ROWS
    # 每个主体恰好一条采信余额，绝不把同一笔本年累计重复相加。
    clusters = {}
    for row in adopted:
        clusters.setdefault(row["cluster_id"], 0)
        clusters[row["cluster_id"]] += 1
    assert all(count == 1 for count in clusters.values())


def test_amount_authority_prefers_balance_source(run):
    """ERP-D 是金额权威来源：同主体存在 ERP-D 余额时，金账采用 ERP-D 而非主档 ERP-A。"""
    service.ensure_golden(run["run_id"])
    golden = _golden_index(run["run_id"])
    target = golden["C-0001"]
    assert target["amount_erp"] == "ERP-D"
    assert target["amount_supplier_id"] == "S-0052"
    assert Decimal(target["ytd_amount"]) == Decimal("3000.00")
    # 主档 ERP-A 的 128900.00 与 ERP-C 的 12890.00 都被抑制，不进合计。
    ledger = {row["supplier_id"]: row for row in store.list_amount_ledger(run["run_id"], "C-0001")}
    assert ledger["S-0001"]["decision"] == "suppressed"
    assert ledger["S-0040"]["decision"] == "suppressed"
    assert ledger["S-0052"]["decision"] == "adopted"


def test_amount_falls_back_when_no_balance_source(run):
    """金额来源没有覆盖的主体，回落到主档类来源余额，仍然只计一次。"""
    service.ensure_golden(run["run_id"])
    ledger = store.list_amount_ledger(run["run_id"], "C-0021")
    adopted = [row for row in ledger if row["decision"] == "adopted"]
    assert len(adopted) == 1
    assert adopted[0]["erp"] == "ERP-A"
    assert Decimal(adopted[0]["observed_amount"]) == Decimal("152300.00")


def test_untrusted_bank_account_never_enters_golden(run):
    """ERP-D 对账户不可信：即便它带账号也不能进付款主数据。"""
    assert FIELD_TRUST["ERP-D"]["bank_account"] == 0
    service.ensure_golden(run["run_id"])
    golden = _golden_index(run["run_id"])
    for row in golden.values():
        if row["bank_account"]:
            prov = _prov_index(run["run_id"])[(row["cluster_id"], "bank_account")]
            assert prov["chosen_erp"] in ("ERP-A", "ERP-B", "ERP-C")
    # 只有 ERP-D（无主档账户）的主体账户为空且阻断付款。
    d_only = golden["C-0002"]
    assert d_only["bank_account"] == ""
    assert d_only["account_verified"] == 0
    assert d_only["payment_blocked"] == 1


def test_tax_conflict_blocks_payment(run):
    """簇内多个可信税号：身份未核实，金账保留一个候选税号但强制阻断付款。"""
    service.ensure_golden(run["run_id"])
    golden = _golden_index(run["run_id"])
    target = golden[EXP.CONFLICT_CLUSTER_ID]
    assert target["payment_blocked"] == 1
    assert "G_TAX_PENDING" in target["block_reasons"]
    pending = [row for row in store.list_golden_pending(run["run_id"])
               if row["reason_code"] == "G_TAX_PENDING"]
    assert len(pending) == EXP.GOLDEN_PENDING
    assert {row["cluster_id"] for row in pending} == {EXP.CONFLICT_CLUSTER_ID}


def test_payment_ready_count(run):
    summary = service.ensure_golden(run["run_id"])
    assert summary["payment_ready"] == EXP.GOLDEN_PAYMENT_READY
    assert summary["payment_blocked"] == EXP.GOLDEN_PAYMENT_BLOCKED
    golden = store.list_golden(run["run_id"])
    assert sum(1 for row in golden if not row["payment_blocked"]) == EXP.GOLDEN_PAYMENT_READY


def test_every_field_has_provenance(run):
    """页面与导出要求：每个字段都能看清采用了哪里。"""
    service.ensure_golden(run["run_id"])
    provenance = store.list_provenance(run["run_id"])
    per_cluster = {}
    for row in provenance:
        per_cluster.setdefault(row["cluster_id"], set()).add(row["field"])
    expected_fields = {"raw_name", "tax_id", "city", "bank_account", "supplier_no", "ytd_amount"}
    for cluster_id, fields in per_cluster.items():
        assert fields == expected_fields, cluster_id


def test_late_correction_takes_over_only_its_field():
    """同号两行：晚到更正只接管自己可信的字段，其余字段保持原值。"""
    base = {
        "supplier_id": "S-1", "supplier_no": "0001", "raw_name": "旧名称有限公司",
        "tax_id": "91110108OLD", "city": "大连", "bank_account": "6222000000000001",
        "erp": "ERP-D", "ref_master_id": "", "ytd_amount": "100.00",
        "last_update": "2026-03-01", "source_file": "d.csv", "line_no": 2,
    }
    incoming = {
        "supplier_id": "S-1", "supplier_no": "0001", "raw_name": "旧名称有限公司",
        "tax_id": "91110108OLD", "city": "沈阳", "bank_account": "",
        "erp": "ERP-D", "ref_master_id": "", "ytd_amount": "100.00",
        "last_update": "2026-03-28", "source_file": "d.csv", "line_no": 5,
    }
    merged, actions = merge.merge_same_id(base, incoming)
    assert merged["city"] == "沈阳"  # 同级、更新时间更新，接管
    assert merged["bank_account"] == "6222000000000001"  # 未受影响
    assert merged["raw_name"] == "旧名称有限公司"
    assert any(item["field"] == "city" and item["reason_code"] == "G_FIELD_OVERRIDDEN"
               for item in actions)


def test_lower_trust_source_cannot_overwrite_master_field():
    """ERP-D 的名称可信度低于 ERP-A：即便晚到也不能覆盖主档名称。"""
    base = {
        "supplier_id": "S-2", "supplier_no": "0002", "raw_name": "主档标准名称有限公司",
        "tax_id": "91110108MA01", "city": "北京", "bank_account": "6222000000000002",
        "erp": "ERP-A", "ref_master_id": "", "ytd_amount": "100.00",
        "last_update": "2026-03-01", "source_file": "a.csv", "line_no": 2,
    }
    incoming = {
        "supplier_id": "S-2", "supplier_no": "0002", "raw_name": "金额侧简写名称",
        "tax_id": "91110108MA01", "city": "北京", "bank_account": "",
        "erp": "ERP-D", "ref_master_id": "", "ytd_amount": "120.00",
        "last_update": "2026-03-30", "source_file": "d.csv", "line_no": 9,
    }
    merged, actions = merge.merge_same_id(base, incoming)
    assert merged["raw_name"] == "主档标准名称有限公司"
    # 但金额按权威级别采用 ERP-D 的更新余额。
    assert Decimal(merged["ytd_amount"]) == Decimal("120.00")


def test_rerun_produces_identical_golden(db):
    """同一批重跑：金账、溯源、账本、待核对完全一致，合计不变。"""
    from app.modules.m1_intake import service as intake_service

    first = intake_service.ingest(force=True)
    service.ensure_golden(first["run_id"])
    snapshot = {
        "golden": store.list_golden(first["run_id"]),
        "provenance": store.list_provenance(first["run_id"]),
        "ledger": store.list_amount_ledger(first["run_id"]),
        "pending": store.list_golden_pending(first["run_id"]),
    }
    second = intake_service.ingest(force=True)
    assert second["run_id"] == first["run_id"]
    summary = service.ensure_golden(second["run_id"])
    assert store.list_golden(second["run_id"]) == snapshot["golden"]
    assert store.list_provenance(second["run_id"]) == snapshot["provenance"]
    assert store.list_amount_ledger(second["run_id"]) == snapshot["ledger"]
    assert store.list_golden_pending(second["run_id"]) == snapshot["pending"]
    assert Decimal(summary["amount_payable"]) == EXP.GOLDEN_PAYABLE


def test_golden_api(client):
    response = client.get("/api/golden")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["subjects"] == EXP.GOLDEN_SUBJECTS
    assert len(body["items"]) == EXP.GOLDEN_SUBJECTS
    detail = client.get("/api/golden", params={"cluster_id": "C-0001"})
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["golden"]["cluster_id"] == "C-0001"
    assert {row["field"] for row in payload["provenance"]} == \
        {"raw_name", "tax_id", "city", "bank_account", "supplier_no", "ytd_amount"}


def test_golden_recomputes_after_review_decision(run):
    """复核决定改变簇划分后，金账主体数与合计必须立即同步，不留旧簇残数据。"""
    from app.modules.m6_reviews import service as review_service

    service.ensure_golden(run["run_id"])
    before = len(store.list_golden(run["run_id"]))
    review_service.decide(run["run_id"], EXP.PAIR_CITY_MISMATCH, "merge", 0)
    after_golden = store.list_golden(run["run_id"])
    assert len(after_golden) == before - 1
    # 溯源与账本仍按新簇完整覆盖，每个主体恰好一条采信余额。
    ledger = store.list_amount_ledger(run["run_id"])
    adopted_clusters = {row["cluster_id"] for row in ledger if row["decision"] == "adopted"}
    golden_clusters = {row["cluster_id"] for row in after_golden}
    assert adopted_clusters == golden_clusters
    # 去重守恒依旧成立。
    summary = service.summary(run["run_id"])
    assert Decimal(summary["amount_payable"]) + Decimal(summary["amount_suppressed"]) \
        == Decimal(summary["amount_observed"])


def test_trust_profile_wiring():
    assert trust.trust_level("ERP-A", "bank_account") == 3
    assert trust.trust_level("ERP-D", "bank_account") == 0
    assert trust.amount_authority("ERP-D") > trust.amount_authority("ERP-A")
    assert trust.is_account_trusted("ERP-A") is True
    assert trust.is_account_trusted("ERP-D") is False


def test_amount_choice_is_order_independent():
    import itertools

    rows = [
        {"supplier_id": "S-1", "erp": "ERP-D", "raw_name": "d", "tax_id": "", "city": "",
         "bank_account": "", "ytd_amount": "3000.00", "last_update": "2026-03-28"},
        {"supplier_id": "S-2", "erp": "ERP-A", "raw_name": "a", "tax_id": "T", "city": "北京",
         "bank_account": "6222", "ytd_amount": "99.00", "last_update": "2026-03-01"},
        {"supplier_id": "S-3", "erp": "ERP-B", "raw_name": "b", "tax_id": "T", "city": "北京",
         "bank_account": "6222", "ytd_amount": "12.00", "last_update": "2026-03-21"},
    ]
    winners = {trust.choose_amount(list(perm))[0][0]["supplier_id"]
               for perm in itertools.permutations(rows)}
    assert winners == {"S-1"}
    assert trust.pick_field(rows, "raw_name")["erp"] == "ERP-A"


def test_zero_balance_never_overrides_real_amount():
    rows = [
        {"supplier_id": "S-9", "erp": "ERP-D", "raw_name": "x", "tax_id": "", "city": "",
         "bank_account": "", "ytd_amount": "0.00", "last_update": "2026-03-29"},
        {"supplier_id": "S-8", "erp": "ERP-C", "raw_name": "x", "tax_id": "", "city": "",
         "bank_account": "", "ytd_amount": "5600.00", "last_update": "2026-03-25"},
    ]
    chosen, _ = trust.choose_amount(rows)
    assert chosen[0]["supplier_id"] == "S-8"
    assert chosen[1] == Decimal("5600.00")


def test_tie_break_is_deterministic():
    rows = [
        {"supplier_id": "S-5", "erp": "ERP-B", "raw_name": "x", "tax_id": "", "city": "",
         "bank_account": "", "ytd_amount": "100.00", "last_update": "2026-03-21"},
        {"supplier_id": "S-4", "erp": "ERP-B", "raw_name": "x", "tax_id": "", "city": "",
         "bank_account": "", "ytd_amount": "100.00", "last_update": "2026-03-21"},
    ]
    chosen, _ = trust.choose_amount(rows)
    assert chosen[0]["supplier_id"] == "S-4"
