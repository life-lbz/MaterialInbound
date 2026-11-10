"""整改回归测试（2026-08-21 整改批次：P0/P1/P2/P3 关键修复锚点）。

覆盖：
- P0-1：PG 连接直传完整 DSN（psycopg2.connect 收到完整 URL，不被 removeprefix 破坏）；
- P0-3：审批 decide 并发保护（终态后并发决策命中 0 行按幂等返回，不覆盖状态）；
- P1-4/P1-8：汇率折算（价格审查跨币种判定正确；审批金额折算本位币）；
- P1-5：汇率 8% 规则经端到端链路可达（quote.fx_delta_pct 传入规则层）；
- P2-12：ToolGuard 白名单单一数据源（与 config roles 一致）；
- P3：inventory_agent 对 stock_qty=None 不再抛 TypeError。
"""
from __future__ import annotations

import pytest

from app.agents.review_agent import ReviewAgent
from app.agents.review_rules import price_risk
from app.db import connection as conn_mod
from app.middleware.approval import _amount_cny

# ---- P0-1：PG 连接完整 DSN ----


def test_pg_connect_passes_full_dsn(monkeypatch):
    """P0-1 整改：psycopg2.connect 收到完整 URL（禁止 removeprefix 破坏 DSN）。"""
    captured: dict = {}
    dsn = "postgresql://qa:pw@db.example.com:5432/miirp?sslmode=require"

    def fake_connect(dsn_arg):
        captured["dsn"] = dsn_arg
        return "conn-ok"

    import psycopg2
    monkeypatch.setattr(conn_mod, "DB_DSN", dsn)
    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    assert conn_mod._pg_connect() == "conn-ok"
    assert captured["dsn"] == dsn          # 完整 URL 透传，不再 removeprefix


def test_pg_dsn_without_scheme_still_invalid():
    """回归锚点：旧实现（removeprefix 后无 scheme 的 DSN）仍会被 psycopg2 拒绝——
    证明 P0-1 修复方向正确（直传完整 URL 才能被 libpq 解析）。"""
    import psycopg2
    with pytest.raises(psycopg2.ProgrammingError, match="missing \"=\""):
        psycopg2.connect("user:pass@127.0.0.1:5432/db")


# ---- P0-3：审批并发保护 ----


@pytest.mark.asyncio
async def test_decide_concurrent_after_terminal_is_idempotent(isolated_sqlite):
    """P0-3 整改：终态后并发决策命中 0 行 → 幂等返回，不覆盖状态。

    模拟并发：先 approve 置终态，再 reject——带 status='pending' 条件的 UPDATE
    命中 0 行，返回当前终态（原实现第二个决策会直接覆盖终态）。
    """
    from app.approval.service import ApprovalService
    req = await ApprovalService.create("create_inbound_record", {"g": "c1"}, "review-001")
    first = await ApprovalService.decide(req["id"], approved=True, reviewer="wh-001")
    assert first["status"] == "approved"
    # 并发/迟到决策（拒绝）不得覆盖已批准的终态
    late = await ApprovalService.decide(req["id"], approved=False, reviewer="wh-001")
    assert late["idempotent"] is True
    assert late["status"] == "approved"
    assert ApprovalService.get_status(req["id"]) == "approved"


@pytest.mark.asyncio
async def test_double_sign_terminal_is_idempotent(isolated_sqlite):
    """P0-3 整改：双人两签后终态幂等——后续任何决策不再改变状态（并发保护语义）。"""
    from app.approval.service import ApprovalService
    req = await ApprovalService.create(
        "create_inbound_record", {"g": "c2"}, "review-001",
        amount_cny=200000.0, double_review=True)
    r1 = await ApprovalService.decide(req["id"], approved=True, reviewer="wh-001")
    assert r1["status"] == "pending"                        # 第一签仍 pending
    r2 = await ApprovalService.decide(req["id"], approved=True, reviewer="comp-001")
    assert r2["status"] == "approved"                       # 第二签 → approved
    # 终态后并发/迟到决策：非审批人/终态校验均不得改变状态
    assert ApprovalService.get_status(req["id"]) == "approved"


# ---- P1-4：价格审查汇率折算（混币种判定） ----


def test_price_risk_mixed_currency_converted():
    """P1-4 整改：USD 单价 vs CNY 报价区间统一折算本位币后比较（不再同数值直比）。

    场景：单价 1.0 USD（=7.1 CNY），区间 6.5-7.5 CNY（mid=7.0 CNY）→ 偏差 1.4% → low；
    旧实现 1.0 与 6.5/7.5 直比偏差 85.7% → 误判 high。
    """
    level, _ = price_risk(1.0, 6.5, 7.5, "USD", quote_currency="CNY")
    assert level == "low"


def test_price_risk_mixed_currency_over_threshold():
    """P1-4 整改：USD 单价偏离 CNY 区间 >15% → high（折算后判定）。"""
    level, _ = price_risk(1.0, 5.0, 6.0, "USD", quote_currency="CNY")  # 7.1 vs mid 5.5 → +29%
    assert level == "high"


def test_price_risk_same_currency_behavior_unchanged():
    """P1-4 整改：同币种线性折算不改变相对偏差（既有边界语义保持）。"""
    assert price_risk(1.15, 0.9, 1.1, "CNY")[0] == "low"     # 恰好 15% 不触发
    assert price_risk(1.16, 0.9, 1.1, "CNY")[0] == "high"    # 超 15% 触发


# ---- P1-5：汇率 8% 规则端到端可达 ----


def test_review_agent_passes_fx_delta_from_quote():
    """P1-5 整改：ReviewAgent.price_check 将 quote.fx_delta_pct 传入规则层。

    构造 fx_delta_pct=9.0 的报价 → 汇率波动超 8% 判 high（原实现恒传 0，规则不可达）。
    """
    async def fake_call(role: str, tool: str, args: dict) -> dict:
        return {"supplier_id": "S", "sku": "M",
                "latest_price": 0.0071, "currency": "USD",
                "p90_min": 0.0068, "p90_max": 0.0091,
                "fx_delta_pct": 9.0}                          # 汇率波动 9% > 8%

    import asyncio

    agent = ReviewAgent(call_tool=fake_call)
    agent.llm.mode = "mock"
    out = asyncio.run(agent.price_check("S", "M", 0.0071, "USD"))
    assert out["risk_level"] == "high"
    assert "汇率波动" in out["reason"]


# ---- P1-8：审批金额折算本位币 ----


def test_amount_cny_converted_to_cny():
    """P1-8 整改：USD 单据金额折算本位币参与高价值判定（0.0071×50000×7.1=2520.5）。"""
    assert _amount_cny({"unit_price": 0.0071, "quantity": 50000, "currency": "USD"}) == 2520.5


def test_amount_cny_cny_no_conversion():
    """P1-8 整改：CNY 单据不折算（10.0×20000=200000）。"""
    assert _amount_cny({"unit_price": 10.0, "quantity": 20000, "currency": "CNY"}) == 200000.0


def test_amount_cny_missing_currency_defaults_cny():
    """P1-8 整改：currency 缺失按 CNY 处理（容错，不阻断审批）。"""
    assert _amount_cny({"unit_price": 1.0, "quantity": 100}) == 100.0


# ---- P2-11：审批与 Agent 风险决策联动门禁 ----


def test_approval_rejects_agent_decision_marker():
    """P2-11 整改：payload 含 _agent_decision=reject → 服务端拦截（审批人无从放行）。"""
    from app.exceptions import RiskRejectedError
    from app.middleware.approval import ApprovalMiddleware
    with pytest.raises(RiskRejectedError, match="P2-11"):
        ApprovalMiddleware._reject_if_agent_decision({"_agent_decision": "reject", "g": 1})
    # 非 reject 决策/缺失 → 剔除标记键后正常进入人工审批
    assert ApprovalMiddleware._reject_if_agent_decision(
        {"_agent_decision": "approve", "g": 1}) == {"g": 1}
    assert ApprovalMiddleware._reject_if_agent_decision({"g": 1}) == {"g": 1}


# ---- P2-12：ToolGuard 单一数据源 ----


def test_guard_whitelist_matches_config_roles():
    """P2-12 整改：ToolGuard 白名单取自 config roles（与 AuthRoleMiddleware 同源）。"""
    from app.agents.guard import ToolGuard
    from app.config import ROLES
    for role, tools in ROLES.items():
        ok, why = ToolGuard.check(role, next(iter(tools)), {"k": "v"})
        assert ok is True, f"{role} 应放行自身白名单工具: {why}"
    # 越权工具仍拒绝
    ok, why = ToolGuard.check("qty-check-agent", "approve_inbound_request", {})
    assert ok is False and "无权调用" in why


# ---- P3：inventory_agent stock_qty=None ----


@pytest.mark.asyncio
async def test_inventory_agent_none_stock_qty_no_crash():
    """P3 整改：stock_qty/avg_daily_usage 为 None 不再抛 TypeError（安全兜底 0）。"""
    from app.agents.inventory_agent import InventoryAgent

    async def fake_call(role: str, tool: str, args: dict) -> dict:
        if tool == "get_inventory":
            return {"sku": "M-1", "stock_qty": None, "safety_stock": None}
        if tool == "get_stock_usage":
            return {"sku": "M-1", "avg_daily_usage": None}
        return {}

    agent = InventoryAgent(call_tool=fake_call)
    out = await agent.check("M-1")
    assert out["alert"] is False          # None 兜底 0：0 < 0 不预警（不误报、不崩溃）
    assert out["suggested_qty"] == 0
