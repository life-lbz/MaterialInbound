"""审查规则引擎：确定性实现（mock 模式与评测共用，保证可复现）。

阈值复用 app/services/rules.py 集中表达（开发文档 §2.4，禁止魔法数字散落多处）：
数量 5%（QTY_TOLERANCE_RATIO）、价格 15%（PRICE_DEVIATION_RATIO）、
汇率 8%（FX_FLUCTUATION_RATIO）、准时率 80%（ONTIME_RATE_THRESHOLD）、
合格率 95%（PASS_RATE_THRESHOLD）、风险等级 high（RISK_LEVEL_HIGH）；
资质过期判定复用 is_qualification_expired（空有效期视为过期，不可信）。

规则函数统一返回 (risk_level, reason) 二元组，上游包装为固定 JSON：
{"risk_level": "high|low", "reason": "..."}（能力说明 §5、api_contract §7）。
"""
from __future__ import annotations

from app.services.fx_service import to_cny
from app.services.rules import (
    FX_FLUCTUATION_RATIO,
    ONTIME_RATE_THRESHOLD,
    PASS_RATE_THRESHOLD,
    PRICE_DEVIATION_RATIO,
    QTY_TOLERANCE_RATIO,
    RISK_LEVEL_HIGH,
    is_qualification_expired,
)


def qty_risk(received_qty: float, po_qty: float) -> tuple[str, str]:
    """数量核验：GRN 实收 vs PO 数量，超收/缺量 >5% 判高风险（§2.4）。"""
    delta = (received_qty - po_qty) / po_qty if po_qty else 1.0
    if abs(delta) > QTY_TOLERANCE_RATIO:
        return "high", f"数量偏差 {delta*100:.1f}% 超阈值 5%"
    return "low", f"数量偏差 {delta*100:.1f}% 在阈值内"


def price_risk(unit_price: float, p90_min: float, p90_max: float, currency: str,
               fx_delta_pct: float = 0.0, quote_currency: str | None = None) -> tuple[str, str]:
    """价格审查：单价（折算本位币）偏离近 90 天报价区间中线 >15%，或进口物料汇率波动 >8% 判高风险。

    P1-4 整改：单价与报价区间统一经 fx_service.to_cny 折算为人民币后再比较
    （"单价（折算本位币）vs 90 天区间"，开发文档 §2.4），消除混币种直比失真。
    - currency：单据币种（单价）；
    - quote_currency：报价区间币种（默认与 currency 相同；跨币种报价时显式传入，
      由 review_agent.price_check 从 quote["currency"] 提取）。
    fx_delta_pct 为百分比数值（如 9.0 表示 9%），与阈值集中表达 FX_FLUCTUATION_RATIO=0.08
    换算比较（保持阈值单一事实源）。
    """
    # 统一折算本位币：单据币种与报价币种可能不同（混币种单据判定正确；
    # 同币种线性折算不改变相对偏差，既有边界语义保持）
    qc = quote_currency or currency
    price_cny = to_cny(unit_price, currency)
    mid_cny = (to_cny(p90_min, qc) + to_cny(p90_max, qc)) / 2
    deviation = (price_cny - mid_cny) / mid_cny if mid_cny else 1.0
    if abs(deviation) > PRICE_DEVIATION_RATIO:
        return "high", f"单价偏离区间中线 {deviation*100:.1f}% 超阈值 15%"
    if currency != "CNY" and abs(fx_delta_pct) > FX_FLUCTUATION_RATIO * 100:
        return "high", f"汇率波动 {fx_delta_pct}% 超阈值 8%"
    return "low", "价格在区间内"


def supplier_risk(profile: dict) -> tuple[str, str]:
    """供应商审查：资质过期、风险等级 high、准时率 <80% 或合格率 <95% 任一命中判高风险。

    档案缺失或为空视为高风险（无记录不可信）；资质有效期缺失视为已过期（rules.py 语义）。
    """
    if not profile:
        return "high", "供应商档案缺失"
    if profile.get("risk_level") == RISK_LEVEL_HIGH:
        return "high", "供应商风险等级 high"
    ontime = profile.get("ontime_rate", 1.0)
    if ontime < ONTIME_RATE_THRESHOLD:
        return "high", f"准时率 {ontime} 低于 80%"
    pass_rate = profile.get("pass_rate", 1.0)
    if pass_rate < PASS_RATE_THRESHOLD:
        return "high", f"合格率 {pass_rate} 低于 95%"
    if is_qualification_expired(profile.get("qualification_expiry", "")):
        return "high", "供应商资质已过期"
    return "low", "供应商资质与绩效正常"


def trace_risk(trace: dict) -> tuple[str, str]:
    """批次追溯：追溯链断裂（无供应商生产批或生产日期缺失）判高风险。"""
    if not trace.get("trace_complete"):
        return "high", "追溯链断裂（缺供应商生产批或生产日期）"
    return "low", "追溯链完整"


def compliance_decision(risks: dict[str, str]) -> str:
    """四路结论汇总：任一 high → reject；含 review/missing → review；否则 approve。"""
    if "high" in risks.values():
        return "reject"
    if any(v in ("review", "missing") for v in risks.values()):
        return "review"
    return "approve"
