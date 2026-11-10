"""A5 ReviewAgent 单测（M3-02 验收：c01-c04 决策语义，能力说明 §7/§9.1）。

- 四路专家 asyncio.gather 并行 + 合规复核汇总，返回
  {correlation_id, decision, risks, details}（api_contract §7）；
- 数据缺失/工具异常 → 该路 high（防漏放行）；LLM 输出解析失败兜底 high/review；
- mock 模式走确定性规则引擎（review_rules），评测 c01-c04 语义一致。
"""
from __future__ import annotations

import re

import pytest

from app.agents.review_agent import ReviewAgent, _parse_decision, _parse_expert

_PASS_DATA = {
    "get_po": {"po_no": "PO-20260818001", "sku": "M-1001", "quantity": 50000},
    "get_supplier_quote": {"supplier_id": "SUP-02", "sku": "M-1001",
                           "p90_min": 0.0068, "p90_max": 0.0091},
    "get_supplier_profile": {"supplier_id": "SUP-02", "qualification_expiry": "2099-01-01",
                             "risk_level": "low", "ontime_rate": 0.98, "pass_rate": 0.99},
    "get_trace_batch": {"batch_no": "B20260820011", "trace_complete": True},
}

_REVIEW_ARGS = {
    "po_no": "PO-20260818001", "grn_no": "GRN-20260820-011", "iqc_no": "IQC-20260820-011",
    "sku": "M-1001", "supplier_id": "SUP-02", "batch_no": "B20260820011",
    "quantity": 50000, "unit_price": 0.0071, "currency": "USD",
}


def _make_call_tool(data=None, raise_tools=()):
    data = dict(data or _PASS_DATA)

    async def fake(role, tool, args):
        if tool in raise_tools:
            raise RuntimeError(f"boom: {tool}")
        return data.get(tool, {})
    return fake


class _FakeLLM:
    """替身 LLM：llm 模式按调用顺序返回响应或抛异常（不发起真实网络请求）。"""

    mode = "llm"

    def __init__(self, responses=None, exc=None):
        self._responses = list(responses or [])
        self._exc = exc

    async def chat(self, system, user, temperature=0.0):
        if self._exc is not None:
            raise self._exc
        return self._responses.pop(0)


# ---- 合规决策（mock 模式，c01-c04 语义）----
@pytest.mark.asyncio
async def test_review_approve_all_low():
    """四路全低风险 → decision=approve；结构符合 api_contract §7。"""
    agent = ReviewAgent(_make_call_tool())
    result = await agent.review(**_REVIEW_ARGS)
    assert result["decision"] == "approve"
    assert set(result["risks"]) == {"数量", "价格", "供应商", "追溯"}
    assert all(v == "low" for v in result["risks"].values())
    assert set(result["details"]) == {"qty", "price", "supplier", "trace"}
    assert re.fullmatch(r"[0-9a-f]{12}", result["correlation_id"])


@pytest.mark.asyncio
async def test_review_reject_on_any_high():
    """数量超收 6%（50000 → 53000）→ 数量 high → decision=reject。"""
    agent = ReviewAgent(_make_call_tool())
    result = await agent.review(**_REVIEW_ARGS | {"quantity": 53000})
    assert result["risks"]["数量"] == "high"
    assert result["decision"] == "reject"


@pytest.mark.asyncio
async def test_review_missing_po_data_high():
    """PO 数量数据缺失（工具返回 error）→ 数量 high（防漏放行）。"""
    data = dict(_PASS_DATA)
    data["get_po"] = {"error": "TOOL_ERROR:NotFoundError:xxx"}
    agent = ReviewAgent(_make_call_tool(data))
    result = await agent.review(**_REVIEW_ARGS)
    assert result["risks"]["数量"] == "high"
    assert "数据缺失" in result["details"]["qty"]["reason"]


@pytest.mark.asyncio
async def test_review_missing_quote_high():
    data = dict(_PASS_DATA)
    data["get_supplier_quote"] = {}
    agent = ReviewAgent(_make_call_tool(data))
    result = await agent.review(**_REVIEW_ARGS)
    assert result["risks"]["价格"] == "high"
    assert "数据缺失" in result["details"]["price"]["reason"]


@pytest.mark.asyncio
async def test_review_missing_profile_high():
    data = dict(_PASS_DATA)
    data["get_supplier_profile"] = {}
    agent = ReviewAgent(_make_call_tool(data))
    result = await agent.review(**_REVIEW_ARGS)
    assert result["risks"]["供应商"] == "high"


@pytest.mark.asyncio
async def test_review_missing_trace_high():
    data = dict(_PASS_DATA)
    data["get_trace_batch"] = {}
    agent = ReviewAgent(_make_call_tool(data))
    result = await agent.review(**_REVIEW_ARGS)
    assert result["risks"]["追溯"] == "high"


@pytest.mark.asyncio
async def test_review_tool_error_collected():
    """工具层异常收敛为 error 键 → 该路 high（防漏放行）。"""
    agent = ReviewAgent(_make_call_tool(raise_tools=("get_po",)))
    result = await agent.review(**_REVIEW_ARGS)
    assert result["risks"]["数量"] == "high"
    # 工具异常收敛为"证据缺失"语义（_safe_call error → qty_check 缺失兜底 high）
    assert result["details"]["qty"]["reason"] == "PO 数量数据缺失"


@pytest.mark.asyncio
async def test_safe_call_guard_blocked():
    """Agent 侧越权调用被 ToolGuard 拦截 → error 键（GUARD: 前缀）。"""
    agent = ReviewAgent(_make_call_tool())
    out = await agent._safe_call("qty-check-agent", "approve_inbound_request", {})
    assert out["error"].startswith("GUARD:")


# ---- 专家结论 / 合规复核 JSON 解析（解析失败兜底 high / review）----
def test_parse_expert_valid_json():
    assert _parse_expert('{"risk_level": "low", "reason": "ok"}') == {
        "risk_level": "low", "reason": "ok"}


def test_parse_expert_invalid_json_fallback_high():
    out = _parse_expert("not-json")
    assert out["risk_level"] == "high"
    assert "解析失败" in out["reason"]


def test_parse_expert_invalid_level_fallback_high():
    assert _parse_expert('{"risk_level": "critical"}')["risk_level"] == "high"


def test_parse_expert_non_dict_json_fallback_high():
    assert _parse_expert("[1, 2]")["risk_level"] == "high"


def test_parse_expert_none_input_fallback_high():
    assert _parse_expert(None)["risk_level"] == "high"


def test_parse_decision_valid():
    assert _parse_decision('{"decision": "reject"}') == "reject"


def test_parse_decision_invalid_fallback_review():
    assert _parse_decision("not-json") == "review"
    assert _parse_decision('{"decision": "banana"}') == "review"
    assert _parse_decision('["x"]') == "review"


# ---- LLM 模式（llm 调用失败防漏放行）----
@pytest.mark.asyncio
async def test_review_llm_mode_valid_decision():
    """llm 模式：四路专家 + 复核走模型输出（mock chat），采用模型结论。"""
    agent = ReviewAgent(_make_call_tool())
    agent.llm = _FakeLLM(responses=[
        '{"risk_level": "low", "reason": "qty"}',
        '{"risk_level": "low", "reason": "price"}',
        '{"risk_level": "low", "reason": "supplier"}',
        '{"risk_level": "low", "reason": "trace"}',
        '{"decision": "approve", "summary": "ok"}',
    ])
    result = await agent.review(**_REVIEW_ARGS)
    assert result["decision"] == "approve"
    assert all(v == "low" for v in result["risks"].values())


@pytest.mark.asyncio
async def test_review_llm_mode_error_fallback():
    """llm 调用失败：专家兜底 high、复核兜底 review（防漏放行，不因 LLM 故障放行）。"""
    agent = ReviewAgent(_make_call_tool())
    agent.llm = _FakeLLM(exc=RuntimeError("llm down"))
    result = await agent.review(**_REVIEW_ARGS)
    assert result["decision"] == "review"
    assert all(v == "high" for v in result["risks"].values())
