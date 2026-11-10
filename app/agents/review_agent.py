"""多 Agent 入库审查编排。

架构（能力说明 §7）：
- 四路专家 Agent（数量/价格/供应商/追溯）：各自持有系统 Prompt 与单一工具，
  并行采集证据并给出 risk_level 结论（LLM 模式由模型判定，mock 模式由规则引擎判定）。
- 合规复核 Agent：汇总四路结论，按内控规则输出 approve | review | reject。
- 工具调用经 ToolGuard 校验（防注入）；LLM 输出解析失败兜底 high/review（防漏放行）。

运行形态：agent 经 call_tool 回调调用工具层（与生产一致），不直接访问数据库；
证据、决策、审批均以 correlation_id 关联（api_contract §7）。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from app.agents.guard import ToolGuard
from app.agents.llm_client import LLMClient, is_llm_mode
from app.agents.prompts import (
    PRICE_CHECK_SYSTEM,
    QTY_CHECK_SYSTEM,
    REVIEW_SYSTEM,
    SUPPLIER_CHECK_SYSTEM,
    TRACE_CHECK_SYSTEM,
)
from app.agents.review_rules import (
    compliance_decision,
    price_risk,
    qty_risk,
    supplier_risk,
    trace_risk,
)

CallTool = Callable[[str, str, dict[str, Any]], Coroutine[Any, Any, dict]]


def _expert_fallback(reason: str = "LLM 输出解析失败，按高风险处理") -> dict:
    """专家结论兜底：high（防漏放行，协作方案 §3.6）。"""
    return {"risk_level": "high", "reason": reason}


def _parse_expert(raw: str) -> dict:
    """解析专家 JSON 输出；非 JSON / 结构非法 / 缺 risk_level 一律兜底 high。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _expert_fallback()
    level = data.get("risk_level") if isinstance(data, dict) else None
    if level in ("high", "low"):
        return {"risk_level": level, "reason": str(data.get("reason", ""))}
    return _expert_fallback()


def _parse_decision(raw: str) -> str:
    """解析合规复核 JSON 输出；解析失败兜底 review（转人工，防漏放行）。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "review"
    decision = data.get("decision") if isinstance(data, dict) else None
    if decision in ("approve", "review", "reject"):
        return decision
    return "review"


class ReviewAgent:
    """四路专家并行审查 + 合规复核汇总；correlation_id 贯穿决策/审批/审计。"""

    def __init__(self, call_tool: CallTool):
        self.call_tool = call_tool          # async (role, tool, args) -> dict
        self.llm = LLMClient()
        self.correlation_id = uuid.uuid4().hex[:12]

    async def _safe_call(self, role: str, tool: str, args: dict) -> dict:
        """工具调用统一过 ToolGuard；工具层异常收敛为 error 键（数据缺失语义）。"""
        ok, why = ToolGuard.check(role, tool, args)
        if not ok:
            return {"error": f"GUARD:{why}"}
        try:
            return await self.call_tool(role, tool, args)
        except Exception as exc:  # noqa: BLE001 —— 工具层异常统一收敛为"证据缺失"
            return {"error": f"TOOL_ERROR:{type(exc).__name__}:{exc}"}

    async def _llm_expert(self, system: str, payload: dict) -> dict:
        """LLM 模式专家判定：网络调用/输出解析异常统一兜底 high（防漏放行）。"""
        try:
            raw = await self.llm.chat(system, json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            return _expert_fallback(f"LLM 调用失败({type(exc).__name__})，按高风险处理")
        return _parse_expert(raw)

    async def qty_check(self, po_no: str, received_qty: float) -> dict:
        po = await self._safe_call("qty-check-agent", "get_po", {"po_no": po_no})
        po_qty = po.get("quantity")
        if "error" in po or po_qty is None:
            return {"risk_level": "high", "reason": "PO 数量数据缺失"}
        if is_llm_mode(self.llm.mode):
            return await self._llm_expert(
                QTY_CHECK_SYSTEM, {"po_qty": po_qty, "received_qty": received_qty})
        level, reason = qty_risk(received_qty, po_qty)
        return {"risk_level": level, "reason": reason}

    async def price_check(self, supplier_id: str, sku: str, unit_price: float,
                          currency: str) -> dict:
        quote = await self._safe_call("price-check-agent", "get_supplier_quote",
                                      {"supplier_id": supplier_id, "sku": sku})
        if ("error" in quote or not quote
                or "p90_min" not in quote or "p90_max" not in quote):
            return {"risk_level": "high", "reason": "报价数据缺失"}
        if is_llm_mode(self.llm.mode):
            return await self._llm_expert(
                PRICE_CHECK_SYSTEM,
                {"unit_price": unit_price, "p90_min": quote["p90_min"],
                 "p90_max": quote["p90_max"], "currency": currency})
        # P1-5 整改：报价携带 fx_delta_pct（汇率波动，默认 0），传入规则层使
        # "进口物料汇率波动 >8%" 规则在端到端链路可达（原实现恒传 0，规则为死规则）。
        # P1-4 整改：报价区间币种以 quote["currency"] 为准（单据币种可能不同）。
        level, reason = price_risk(unit_price, quote["p90_min"], quote["p90_max"], currency,
                                   fx_delta_pct=float(quote.get("fx_delta_pct", 0.0)),
                                   quote_currency=str(quote.get("currency") or currency))
        return {"risk_level": level, "reason": reason}

    async def supplier_check(self, supplier_id: str) -> dict:
        profile = await self._safe_call("supplier-check-agent", "get_supplier_profile",
                                        {"supplier_id": supplier_id})
        if "error" in profile or not profile:
            return {"risk_level": "high", "reason": "供应商档案缺失"}
        if is_llm_mode(self.llm.mode):
            return await self._llm_expert(SUPPLIER_CHECK_SYSTEM, profile)
        level, reason = supplier_risk(profile)
        return {"risk_level": level, "reason": reason}

    async def trace_check(self, batch_no: str) -> dict:
        trace = await self._safe_call("trace-check-agent", "get_trace_batch",
                                      {"batch_no": batch_no})
        if "error" in trace or not trace:
            return {"risk_level": "high", "reason": "追溯数据缺失"}
        if is_llm_mode(self.llm.mode):
            return await self._llm_expert(TRACE_CHECK_SYSTEM, trace)
        level, reason = trace_risk(trace)
        return {"risk_level": level, "reason": reason}

    async def review(self, *, po_no: str, grn_no: str, iqc_no: str, sku: str,
                     supplier_id: str, batch_no: str, quantity: int,
                     unit_price: float, currency: str) -> dict:
        """并发执行四路审查 → 合规复核汇总。

        返回 {correlation_id, decision, risks, details}（api_contract §7）；
        决策失败兜底 review（转人工），不因 LLM 故障放行。
        """
        qty, price, supplier, trace = await asyncio.gather(
            self.qty_check(po_no, quantity),
            self.price_check(supplier_id, sku, unit_price, currency),
            self.supplier_check(supplier_id),
            self.trace_check(batch_no),
        )
        risks = {"数量": qty["risk_level"], "价格": price["risk_level"],
                 "供应商": supplier["risk_level"], "追溯": trace["risk_level"]}
        if is_llm_mode(self.llm.mode):
            try:
                raw = await self.llm.chat(
                    REVIEW_SYSTEM, json.dumps(risks, ensure_ascii=False))
            except Exception:  # noqa: BLE001 —— LLM 故障转人工，防漏放行
                decision = "review"
            else:
                decision = _parse_decision(raw)
        else:
            decision = compliance_decision(risks)
        return {
            "correlation_id": self.correlation_id,
            "decision": decision,
            "risks": risks,
            "details": {"qty": qty, "price": price, "supplier": supplier, "trace": trace},
        }
