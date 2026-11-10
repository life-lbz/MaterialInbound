"""A5 InventoryAgent 单测（M3-02 验收：M-1001 锚点 alert=True，能力说明 §8）。

- 线性工具循环：get_inventory → get_stock_usage → 规则决策 →（低于安全线）notify；
- 建议订货量 = avg_daily_usage × replenish_days + (safety_stock - stock_qty)，
  向上取整到 100 的倍数（api_contract §7）；
- ToolGuard 拦截 / 工具异常 → error 键，不抛出；
- LLM 模式输出解析失败回退规则引擎（防漏报，协作方案 §3.6）。
"""
from __future__ import annotations

import pytest

from app.agents.inventory_agent import InventoryAgent

_M1001_STOCK = {"sku": "M-1001", "stock_qty": 12000, "safety_stock": 15000,
                "avg_daily_usage": 620}
_M1001_USAGE = {"sku": "M-1001", "days": 90, "avg_daily_usage": 620, "total_usage": 55800}


def _make_call_tool(stock=None, usage=None, raise_tools=()):
    async def fake(role, tool, args):
        if tool in raise_tools:
            raise RuntimeError(f"boom: {tool}")
        if tool == "get_inventory":
            return stock or dict(_M1001_STOCK)
        if tool == "get_stock_usage":
            return usage or dict(_M1001_USAGE)
        if tool == "notify":
            return {"status": "sent"}
        return {}
    return fake


class _FakeLLM:
    """替身 LLM：llm 模式返回预定响应或抛异常（不发起真实网络请求）。"""

    mode = "llm"

    def __init__(self, responses=None, exc=None):
        self._responses = list(responses or [])
        self._exc = exc

    async def chat(self, system, user, temperature=0.0):
        if self._exc is not None:
            raise self._exc
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_alert_low_stock_m1001():
    """M-1001（12000 < 15000）→ alert=True；建议订货量 620×14+3000=11680 → 11700。"""
    agent = InventoryAgent(_make_call_tool())
    result = await agent.check("M-1001")
    assert result["alert"] is True
    assert result["suggested_qty"] == 11700
    assert "建议订货" in result["reason"]


@pytest.mark.asyncio
async def test_no_alert_normal_stock():
    """库存高于安全线 → alert=False、suggested_qty=0（M-2002: 860 > 500）。"""
    stock = {"sku": "M-2002", "stock_qty": 860, "safety_stock": 500, "avg_daily_usage": 12}
    usage = {"sku": "M-2002", "days": 90, "avg_daily_usage": 12, "total_usage": 1080}
    agent = InventoryAgent(_make_call_tool(stock=stock, usage=usage))
    result = await agent.check("M-2002")
    assert result["alert"] is False
    assert result["suggested_qty"] == 0
    assert "无需订货" in result["reason"]


@pytest.mark.asyncio
async def test_guard_blocked_returns_error():
    """复杂对象参数被 ToolGuard 拦截 → error 键（GUARD: 前缀），不抛出。"""
    agent = InventoryAgent(_make_call_tool())
    out = await agent._safe_call("inventory-agent", "notify", {"body": {"nested": 1}})
    assert out["error"].startswith("GUARD:")


@pytest.mark.asyncio
async def test_tool_error_collected():
    """工具层异常收敛为 error 键（TOOL_ERROR: 前缀），check 不抛出。"""
    agent = InventoryAgent(_make_call_tool(raise_tools=("get_inventory",)))
    result = await agent.check("M-1001")
    assert result["alert"] is False
    assert result["suggested_qty"] == 0
    assert "TOOL_ERROR" in result["reason"]


@pytest.mark.asyncio
async def test_llm_mode_valid_decision():
    """LLM 输出合法 → 采用 LLM 决策（不覆盖规则引擎）。"""
    agent = InventoryAgent(_make_call_tool())
    agent.llm = _FakeLLM(
        responses=['{"alert": true, "suggested_qty": 100, "reason": "llm 建议"}'])
    result = await agent.check("M-1001")
    assert result["alert"] is True
    assert result["suggested_qty"] == 100
    assert result["reason"] == "llm 建议"


@pytest.mark.asyncio
async def test_llm_invalid_output_falls_back_to_rule():
    """LLM 输出非法 → 回退规则引擎重算（防漏报），reason 标注回退来源。"""
    agent = InventoryAgent(_make_call_tool())
    agent.llm = _FakeLLM(responses=["not json at all"])
    result = await agent.check("M-1001")
    assert result["alert"] is True
    assert result["suggested_qty"] == 11700
    assert result["reason"].startswith("LLM 输出解析失败")


@pytest.mark.asyncio
async def test_llm_exc_falls_back_to_rule():
    """LLM 调用抛异常 → 回退规则引擎（防漏报）。"""
    agent = InventoryAgent(_make_call_tool())
    agent.llm = _FakeLLM(exc=RuntimeError("llm down"))
    result = await agent.check("M-1001")
    assert result["alert"] is True
    assert result["suggested_qty"] == 11700
    assert result["reason"].startswith("LLM 输出解析失败")
