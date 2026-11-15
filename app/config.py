"""跨模块唯一来源的常量：路径、规则版本、阈值、列契约、空值哨兵。"""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DIR = BASE_DIR / "sample_data"

APP_NAME = "供应商主数据台账"
RULE_VERSION = "em-1.4.0"

DB_PATH = Path(os.environ.get("MERGE_DB", str(BASE_DIR / "data" / "merge.db")))

# 采信顺序固定，保证同一批文件得到同一个输入摘要与 run_id。
SAMPLE_FILES = (
    "erp_a_primary_utf8.csv",
    "erp_b_alias_bom.csv",
    "erp_c_north_gb18030.csv",
    "erp_d_amounts.csv",
    "erp_e_header_only.csv",
    "erp_f_blank_rows.csv",
)

ENCODING_CHAIN = ("utf-8-sig", "utf-8", "gb18030")

# 列契约：字段名 -> (是否必填, 说明单位)
COLUMNS = (
    ("supplier_id", True),
    ("supplier_no", True),
    ("name", True),
    ("tax_id", False),
    ("city", False),
    ("bank_account", False),
    ("erp_source", False),
    ("ref_master_id", False),
    ("ytd_amount", False),
    ("last_update", False),
)

COLUMN_ALIASES = {
    "供应商编码": "supplier_id",
    "供应商号": "supplier_id",
    "供应商编号": "supplier_no",
    "编码": "supplier_no",
    "名称": "name",
    "供应商名称": "name",
    "税号": "tax_id",
    "纳税人识别号": "tax_id",
    "城市": "city",
    "开户行账号": "bank_account",
    "来源系统": "erp_source",
    "主档号": "ref_master_id",
    "本年累计": "ytd_amount",
    "更新时间": "last_update",
}

# 空值哨兵：集中定义，与"空串"语义分开（空串表示确实没有税号）。
SENTINEL_VALUES = frozenset({"-", "--", "n/a", "na", "null", "none", "未知", "无", "空", "/"})

SIMILARITY_WEIGHT_DICE = Decimal("0.55")
SIMILARITY_WEIGHT_RATIO = Decimal("0.45")
AUTO_THRESHOLD = Decimal("0.92")
REVIEW_THRESHOLD = Decimal("0.80")
SCORE_SCALE = Decimal("0.000001")

MAX_BLOCK_SIZE = 120
MAX_CANDIDATES = 4000

TZ_DISPLAY = "Asia/Shanghai"
PAGE_SIZE = 60

# ---------------------------------------------------------------------------
# 字段级可信来源画像（M8 金账装配）
#
# 每个来源只对自己可信的字段负责：主档来源管身份与账户，金额来源管余额。
# 级别 0 = 该来源对此字段不可信，其值只作证据、永不入主数据；
# 级别越高越优先；同级按更新时间（last_update）降序，再按供应商号升序兜底，
# 因此同批重跑结果与输入文件顺序无关，逐分可复算。
# ---------------------------------------------------------------------------
FIELD_TRUST = {
    "ERP-A": {"raw_name": 3, "tax_id": 3, "city": 3, "bank_account": 3, "supplier_no": 3},
    "ERP-B": {"raw_name": 2, "tax_id": 3, "city": 2, "bank_account": 3, "supplier_no": 2},
    "ERP-C": {"raw_name": 2, "tax_id": 3, "city": 2, "bank_account": 3, "supplier_no": 2},
    # ERP-D 是金额来源：账户字段明确不可信（级别 0），身份字段只作旁证。
    "ERP-D": {"raw_name": 1, "tax_id": 1, "city": 1, "bank_account": 0, "supplier_no": 1},
}

# 同一主体的本年累计是各系统对同一笔余额的快照，不能跨系统相加。
# 金额只在"金额权威来源"里选一条：权威级别高者胜，同级取更新时间最新者。
AMOUNT_AUTHORITY = {
    "ERP-D": 3,
    "ERP-A": 2,
    "ERP-B": 1,
    "ERP-C": 1,
}

# 账户只接受这些来源；其他来源即便带了账号也不进付款主数据。
BANK_ACCOUNT_TRUSTED_SOURCES = ("ERP-A", "ERP-B", "ERP-C")

# 金账装配参与的字段及中文列名（导出与页面共用）。
GOLDEN_FIELDS = ("raw_name", "tax_id", "city", "bank_account", "supplier_no")
GOLDEN_FIELD_LABELS = {
    "raw_name": "名称",
    "tax_id": "税号",
    "city": "城市",
    "bank_account": "开户行账号",
    "supplier_no": "供应商号",
    "ytd_amount": "本年累计",
}
