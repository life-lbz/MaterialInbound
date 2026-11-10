"""A5 review_rules 确定性规则单测（M3-01 验收：阈值边界，能力说明 §5）。

阈值与开发文档 §2.4 逐字一致：数量 5%、价格 15%、汇率 8%、准时率 80%、合格率 95%。
判定语义：恰好等于阈值 → 不判高风险（条件为严格大于/小于）。
"""
from __future__ import annotations

from app.agents.review_rules import (
    compliance_decision,
    price_risk,
    qty_risk,
    supplier_risk,
    trace_risk,
)


def _level(result: tuple[str, str]) -> str:
    return result[0]


# ---- 数量核验（QTY_TOLERANCE_RATIO = 0.05）----
def test_qty_risk_exact_threshold_is_low():
    """恰好偏差 5%（1.05 倍 PO 量）→ low（条件为 >5%）。"""
    assert _level(qty_risk(105, 100)) == "low"


def test_qty_risk_over_threshold_high():
    assert _level(qty_risk(106, 100)) == "high"


def test_qty_risk_shortage_over_threshold_high():
    assert _level(qty_risk(93, 100)) == "high"


def test_qty_risk_zero_po_qty_high():
    """PO 数量缺失（0）时按最大偏差兜底 high。"""
    assert _level(qty_risk(10, 0)) == "high"


# ---- 价格审查（PRICE_DEVIATION_RATIO = 0.15 / FX_FLUCTUATION_RATIO = 0.08）----
def test_price_risk_exact_threshold_is_low():
    """恰好偏离 15% → low（条件为 >15%）。"""
    assert _level(price_risk(1.15, 0.9, 1.1, "CNY")) == "low"


def test_price_risk_over_threshold_high():
    assert _level(price_risk(1.16, 0.9, 1.1, "CNY")) == "high"


def test_price_risk_within_threshold_low():
    assert _level(price_risk(1.0, 0.9, 1.1, "CNY")) == "low"


def test_price_risk_fx_exact_threshold_low():
    """非 CNY 汇率恰好 8% → low（条件为 >8%）。"""
    assert _level(price_risk(1.0, 0.9, 1.1, "USD", fx_delta_pct=8.0)) == "low"


def test_price_risk_fx_over_threshold_high():
    assert _level(price_risk(1.0, 0.9, 1.1, "USD", fx_delta_pct=8.1)) == "high"


def test_price_risk_cny_ignores_fx():
    """CNY（非进口）不参与汇率波动判定。"""
    assert _level(price_risk(1.0, 0.9, 1.1, "CNY", fx_delta_pct=9.0)) == "low"


def test_price_risk_zero_mid_high():
    """报价区间中线为 0（数据缺失）按最大偏离兜底 high。"""
    assert _level(price_risk(1.0, 0, 0, "CNY")) == "high"


# ---- 供应商审查（ONTIME 80% / PASS 95% / 资质过期 / risk_level=high）----
def test_supplier_risk_missing_profile_high():
    assert _level(supplier_risk({})) == "high"


def test_supplier_risk_high_level_high():
    assert _level(supplier_risk({"risk_level": "high"})) == "high"


def test_supplier_risk_low_ontime_high():
    assert _level(supplier_risk({"ontime_rate": 0.79})) == "high"


def test_supplier_risk_exact_ontime_threshold_low():
    """准时率恰好 80% → low。"""
    profile = {"ontime_rate": 0.80, "pass_rate": 0.95, "qualification_expiry": "2099-01-01"}
    assert _level(supplier_risk(profile)) == "low"


def test_supplier_risk_low_pass_rate_high():
    assert _level(supplier_risk({"pass_rate": 0.94})) == "high"


def test_supplier_risk_expired_qualification_high():
    profile = {"ontime_rate": 1.0, "pass_rate": 1.0, "qualification_expiry": "2020-01-01"}
    assert _level(supplier_risk(profile)) == "high"


def test_supplier_risk_invalid_qualification_high():
    """资质有效期非法格式视为过期。"""
    profile = {"ontime_rate": 1.0, "pass_rate": 1.0, "qualification_expiry": "not-a-date"}
    assert _level(supplier_risk(profile)) == "high"


def test_supplier_risk_normal_low():
    profile = {"risk_level": "low", "ontime_rate": 0.98, "pass_rate": 0.99,
               "qualification_expiry": "2099-01-01"}
    assert _level(supplier_risk(profile)) == "low"


# ---- 批次追溯（trace_complete 派生）----
def test_trace_risk_complete_low():
    assert _level(trace_risk({"trace_complete": True})) == "low"


def test_trace_risk_incomplete_high():
    assert _level(trace_risk({"trace_complete": False})) == "high"


def test_trace_risk_missing_high():
    assert _level(trace_risk({})) == "high"


# ---- 合规复核（任一 high → reject；review/missing → review；全 low → approve）----
def test_compliance_decision_reject_on_high():
    risks = {"数量": "high", "价格": "low", "供应商": "low", "追溯": "low"}
    assert compliance_decision(risks) == "reject"


def test_compliance_decision_review_on_review_level():
    risks = {"数量": "review", "价格": "low", "供应商": "low", "追溯": "low"}
    assert compliance_decision(risks) == "review"


def test_compliance_decision_review_on_missing():
    risks = {"数量": "missing", "价格": "low", "供应商": "low", "追溯": "low"}
    assert compliance_decision(risks) == "review"


def test_compliance_decision_approve_all_low():
    risks = {"数量": "low", "价格": "low", "供应商": "low", "追溯": "low"}
    assert compliance_decision(risks) == "approve"
