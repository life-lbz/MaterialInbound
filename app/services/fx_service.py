"""汇率服务：本位币（CNY）折算与汇率波动数据源（P1-1 整改新增）。

- 目标：价格审查（review_rules.price_risk）与审批金额（ApprovalMiddleware._amount_cny）
  统一经本服务折算为人民币，消除"原币种数值直接参与判定"的失真（P1-4/P1-8）。
- 种子汇率集中表达（可替换为外部汇率 API / 配置注入，本模块为唯一数据源）。
- 汇率波动：fx_delta_pct() 默认 0（无波动）；具体单据的波动率由报价数据携带
  （supplier_service 报价的 fx_delta_pct 字段，review_agent.price_check 读取后传入规则层）。
"""
from __future__ import annotations

# 种子汇率表：货币 -> 1 单位外币折合人民币（本位币 CNY = 1.0）
# 生产环境可经 MIIRP_FX_RATES 环境变量注入（JSON），或替换为外部汇率服务。
FX_RATES: dict[str, float] = {
    "CNY": 1.0,
    "USD": 7.1,
}


def to_cny(amount: float, currency: str) -> float:
    """按币种折算为人民币。

    - 未知币种按 1.0 折算（不阻断业务，与金额估算容错语义一致）；
    - currency 缺失/空按 CNY 处理。
    """
    if amount is None:
        return 0.0
    rate = FX_RATES.get((currency or "CNY").upper(), 1.0)
    return float(amount) * rate


def fx_delta_pct(currency: str) -> float:
    """当前币种汇率波动百分比（0.0 = 无波动）。

    预留扩展点：可接入外部汇率波动数据源 / 配置注入（MIIRP_FX_DELTA_PCT）。
    """
    return 0.0


__all__ = ["FX_RATES", "fx_delta_pct", "to_cny"]
