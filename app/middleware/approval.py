"""写操作审批：中间件统一创建审批单（唯一入口，D1），Agent 提议 → 人工确认 → 通过后才执行。

- 唯一入口：审批单只能由本中间件创建（ApprovalService.create），工具/服务不得自行创建（D1 回归）。
- 选项 B（v3.0）：审批通过后把审批单号注入 context state（miirp_approval_request_id），
  工具执行阶段读取并随业务单据落库，实现"业务单 ↔ 审批单"强关联与幂等（data_contract §2）。
- 高价值双人审批：amount_cny = unit_price × quantity ≥ HIGH_VALUE_THRESHOLD_CNY → double_review=1。
- 状态机在 app/approval/service.py（A4，M2-03）：pending → approved/rejected/expired，终态幂等。
- 决策结果：approved → 放行并注入审批单号；rejected → ApprovalRejectedError；
  超时 → ApprovalTimeoutError（api_contract §5）。
- correlation_id 与审计中间件同算法（request_id 优先、uuid 兜底），
  保证 approval_requests ↔ audit_events 三向关联闭环（data_contract §5 口径 #5）。
"""
from __future__ import annotations

import time
import uuid

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from app.approval.service import ApprovalService
from app.config import HIGH_VALUE_THRESHOLD_CNY, WRITE_TOOLS
from app.exceptions import (
    ApprovalRejectedError,
    ApprovalTimeoutError,
    RiskRejectedError,
)
from app.services.fx_service import to_cny


def _amount_cny(payload: dict) -> float | None:
    """估算折合人民币金额（单价 × 数量，按币种折算本位币；P1-8 整改）。

    P1-4/P1-8 统一口径：经 app.services.fx_service.to_cny 折算为人民币
    （与价格审查共用同一汇率数据源），消除"原币种数值直接参与高价值判定"的失真。
    参数缺失或类型非法返回 None（不参与高价值判定，保持单审批语义）。
    """
    try:
        price = float(payload.get("unit_price", 0))
        qty = int(payload.get("quantity", 0))
        currency = str(payload.get("currency", "CNY") or "CNY")
        return to_cny(price * qty, currency)
    except (TypeError, ValueError):
        return None


def _correlation_id(context: MiddlewareContext) -> str:
    """请求链路标识：与审计中间件同算法（request_id 优先、uuid 兜底），保证三向闭环。"""
    fc = context.fastmcp_context
    rc = getattr(fc, "request_context", None) if fc is not None else None
    rid = getattr(rc, "request_id", None)
    return str(rid) if rid else uuid.uuid4().hex[:12]


async def _state(context: MiddlewareContext, key: str) -> str | None:
    """读取 context state（AuthRoleMiddleware 注入）；context 缺失返回 None。"""
    fc = context.fastmcp_context
    if fc is None:
        return None
    return await fc.get_state(key)


class ApprovalMiddleware(Middleware):
    """审批中间件：写工具（WRITE_TOOLS）拦截 + 审批单唯一入口 + 选项 B 状态注入。"""

    # 内部保留键：Agent 编排层可将审查决策随提交写入 payload（P2-11 整改），
    # 值为 "reject" 时服务端直接拦截（系统层门禁），不进入人工审批。
    _AGENT_DECISION_KEY = "_agent_decision"

    @staticmethod
    def _reject_if_agent_decision(payload: dict) -> dict:
        """P2-11 门禁：提取并校验 Agent 风险决策标记，返回剔除标记后的 payload。

        - payload 含 _agent_decision="reject" → 抛 RiskRejectedError（服务端拦截，
          审批人无从放行；编排层提交时随 payload 携带审查决策）；
        - 其余取值（approve/review/缺失）→ 剔除标记键后正常进入人工审批。
        """
        payload = dict(payload or {})
        agent_decision = payload.pop(ApprovalMiddleware._AGENT_DECISION_KEY, None)
        if agent_decision == "reject":
            raise RiskRejectedError(
                "Agent 风险审查未通过（decision=reject），禁止提交入库审批（P2-11 门禁）")
        return payload

    async def on_call_tool(self, context: MiddlewareContext[mt.CallToolRequestParams],
                           call_next: CallNext[mt.CallToolRequestParams, ToolResult]) -> ToolResult:
        params = context.message
        if params.name not in WRITE_TOOLS:
            return await call_next(context)

        # P2-11 整改：Agent 风险决策联动门禁——审查判 reject 的高风险单
        # 服务端直接拒绝，审批人无从放行（系统层门禁）。
        payload = ApprovalMiddleware._reject_if_agent_decision(params.arguments)
        amount = _amount_cny(payload)
        double_review = bool(amount and amount >= HIGH_VALUE_THRESHOLD_CNY)
        client_id = await _state(context, "miirp_client_id") or "unknown"

        # 唯一入口：创建审批单（D1）；工具层不得自行创建（红线，module_boundaries §4）
        request = await ApprovalService.create(
            tool_name=params.name,
            payload=payload,
            proposer=client_id,
            amount_cny=amount,
            double_review=double_review,
            correlation_id=_correlation_id(context),
        )
        # P2-3 整改：记录审批等待时长，供 AuditMiddleware 从工具耗时中扣除
        # （审批等待最长 120s，原实现将其计入审计 latency 污染直方图与 p95）。
        _wait_start = time.perf_counter()
        decision = await ApprovalService.wait_for_decision(request["id"])
        _wait_ms = (time.perf_counter() - _wait_start) * 1000
        fc = context.fastmcp_context
        if fc is not None:
            prev = float((await fc.get_state("miirp_approval_wait_ms")) or 0)
            await fc.set_state("miirp_approval_wait_ms", prev + _wait_ms)
        if decision == "approved":
            # ===== 选项 B：审批通过后注入审批单号，工具执行阶段可读（实机验证）=====
            # 审批人列表可由工具经 ApprovalService 按需查询，不在此冗余注入
            fc = context.fastmcp_context
            if fc is not None:
                await fc.set_state("miirp_approval_request_id", request["id"])
            return await call_next(context)
        if decision == "rejected":
            raise ApprovalRejectedError(f"审批未通过: {request['id']}")
        raise ApprovalTimeoutError(f"审批超时: {request['id']}")
