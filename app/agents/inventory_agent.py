"""单 Agent：安全库存预警与再订货建议。

线性工具循环（能力说明 §8）：查询库存 → 查用量 → 计算建议订货量 →
低于安全线时通知采购。展示基础 tool-calling loop（LLM 模式）与确定性规则（mock 模式）。

建议订货量 = avg_daily_usage × replenish_days + (safety_stock - stock_qty)，
向上取整到 100 的倍数（api_contract §7、能力说明 §4 INVENTORY_SYSTEM）。
LLM 输出解析失败回退确定性规则重算（不因模型故障漏报）。
"""
from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Coroutine
from typing import Any

from app.agents.guard import ToolGuard
from app.agents.llm_client import LLMClient, is_llm_mode
from app.agents.prompts import INVENTORY_SYSTEM

logger = logging.getLogger("miirp.agents.inventory")

CallTool = Callable[[str, str, dict[str, Any]], Coroutine[Any, Any, dict]]

_ROUND = 100  # 建议订货量向上取整粒度


class InventoryAgent:
    """单 Agent 库存预警：线性工具循环，产出 {alert, suggested_qty, reason}。"""

    def __init__(self, call_tool: CallTool):
        self.call_tool = call_tool
        self.llm = LLMClient()

    async def _safe_call(self, role: str, tool: str, args: dict) -> dict:
        """工具调用统一过 ToolGuard；工具层异常收敛为 error 键。"""
        ok, why = ToolGuard.check(role, tool, args)
        if not ok:
            return {"error": f"GUARD:{why}"}
        try:
            return await self.call_tool(role, tool, args)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"TOOL_ERROR:{type(exc).__name__}:{exc}"}

    async def check(self, sku: str, replenish_days: int = 14) -> dict:
        """线性工具循环：库存 → 用量 → 决策 → （低于安全线）通知采购。"""
        stock = await self._safe_call("inventory-agent", "get_inventory", {"sku": sku})
        usage = await self._safe_call("inventory-agent", "get_stock_usage",
                                      {"sku": sku, "days": 90})
        if "error" in stock or "error" in usage:
            return {"alert": False, "suggested_qty": 0,
                    "reason": stock.get("error") or usage.get("error")}
        if is_llm_mode(self.llm.mode):
            result = await self._llm_decide(sku, stock, usage, replenish_days)
        else:
            result = self._rule(stock, usage, replenish_days)
        if result.get("alert"):
            await self._safe_call("inventory-agent", "notify",
                                  {"channel": "internal", "receiver": "buyer",
                                   "title": f"{sku} 低于安全库存",
                                   "body": json.dumps(result, ensure_ascii=False)})
        return result

    async def _llm_decide(self, sku: str, stock: dict, usage: dict,
                          replenish_days: int) -> dict:
        """LLM 模式决策：输出非法/缺失键/调用失败时回退规则引擎重算（防漏报）。"""
        payload = {"sku": sku, **stock, **usage, "replenish_days": replenish_days}
        try:
            raw = await self.llm.chat(INVENTORY_SYSTEM,
                                      json.dumps(payload, ensure_ascii=False))
            data = json.loads(raw)
            if isinstance(data, dict) and "alert" in data and "suggested_qty" in data:
                return {"alert": bool(data["alert"]),
                        "suggested_qty": int(data["suggested_qty"]),
                        "reason": str(data.get("reason", ""))}
            raise ValueError("LLM 输出缺少 alert/suggested_qty 键")
        except Exception as exc:  # noqa: BLE001 —— 解析/网络失败走确定性兜底
            logger.warning("inventory llm 决策失败，回退规则引擎: %s", exc)
        result = self._rule(stock, usage, replenish_days)
        result["reason"] = "LLM 输出解析失败，回退规则引擎: " + result["reason"]
        return result

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        """数值安全转换（P3 整改）：None/非法值兜底 0，防 stock_qty=None 抛 TypeError。"""
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _rule(stock: dict, usage: dict, replenish_days: int) -> dict:
        """确定性规则：库存低于安全线 → 建议订货量向上取整到 100 的倍数。"""
        stock_qty = InventoryAgent._num(stock.get("stock_qty"))
        safety_stock = InventoryAgent._num(stock.get("safety_stock"))
        avg_daily_usage = InventoryAgent._num(usage.get("avg_daily_usage"))
        low = stock_qty < safety_stock
        suggested = 0
        if low:
            base = avg_daily_usage * replenish_days + (safety_stock - stock_qty)
            suggested = int(math.ceil(max(base, 0) / _ROUND) * _ROUND)
        return {"alert": low, "suggested_qty": suggested,
                "reason": f"库存 {stock_qty:.0f} / 安全线 {safety_stock:.0f}"
                          + ("，建议订货" if low else "，无需订货")}
