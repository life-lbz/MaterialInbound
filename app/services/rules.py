"""业务规则阈值集中表达（开发文档 §2.4，禁止魔法数字散落多处）。

本模块是 A4 业务规则的事实来源：所有阈值常量与辅助判定函数唯一出处，
供 services 层与上层（A5 app/agents/review_rules.py）统一引用。
数值与开发文档 §2.4 入库审查规则逐字一致；变更阈值必须先改本文件。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

# ---- 数量核验（§2.4 数量核验）----
# 预警规则：GRN 实收 vs PO 数量，超收 >5% 或缺量 >5%
QTY_TOLERANCE_RATIO = 0.05

# ---- 价格审查（§2.4 价格审查）----
# 预警规则：PO 单价（折算本位币）vs 近 90 天价格区间，偏离 >15%
PRICE_DEVIATION_RATIO = 0.15
# 预警规则：进口物料汇率波动 >8%
FX_FLUCTUATION_RATIO = 0.08

# ---- 供应商审查（§2.4 供应商审查）----
# 预警规则：准时率 <80%
ONTIME_RATE_THRESHOLD = 0.80
# 预警规则：合格率 <95%
PASS_RATE_THRESHOLD = 0.95
# 预警规则：高风险等级（risk_level=high）
RISK_LEVEL_HIGH = "high"

# ---- 批次追溯（§2.4 批次追溯）----
# 预警规则：追溯链断裂（无供应商生产批 supplier_batch 或生产日期 production_date 缺失）

# ---- 合规复核（§2.4 合规复核）----
# 任一高风险项 → 拒绝；中风险 → 转人工；无风险 → 通过（决策编排由 A5 review_rules 实现）


def is_qualification_expired(expiry_iso: str, today: date | None = None) -> bool:
    """资质有效期判定（§2.4 供应商审查：资质过期即预警）。

    expiry_iso 为空视为已过期（无有效期记录不可信）；
    否则按 ISO 日期字符串比较，早于或等于今日（含当日到期）判过期（P2-7 整改：
    语义与注释统一——"含当日到期判过期"，原实现 expiry < today 当日不判）。
    """
    if not expiry_iso:
        return True
    try:
        expiry = date.fromisoformat(expiry_iso)
    except ValueError:
        return True
    today = today or datetime.now(timezone.utc).date()
    return expiry <= today
