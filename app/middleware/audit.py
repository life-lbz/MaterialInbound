"""审计中间件：调用指标 + 结构化审计落库（含失败事件）。

- trace_id 贯穿（D22）：correlation_id 写入 contextvars（logging_setup.set_trace_id），
  日志与审计共享同一 trace_id，支持跨模块对账（data_contract §1.2）。
- 审计摘要值哈希（D14）：args_summary 由 app.audit.store.summarize 生成
  "键名:类型:sha256前8位"，敏感明文不落库。
- 含失败事件：status = "error:<异常类名>"，同时记录错误指标（§6.7）。
- correlation_id 与审批中间件同算法（request_id 优先、uuid 兜底），
  保证 audit_events ↔ approval_requests 三向关联闭环（data_contract §5 口径 #5）。
- 落库旁路保护：审计存储不可用（DB 故障等）时仅告警，不阻断业务调用
  （审计是旁路治理面，不得成为业务可用性放大器）。
"""
from __future__ import annotations

import logging
import time
import uuid

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from app.audit.store import AuditEvent, AuditStore, summarize
from app.logging_setup import set_trace_id
from app.monitoring import record_call, record_error, record_latency

logger = logging.getLogger("miirp.middleware.audit")


async def _client_id(context: MiddlewareContext) -> str:
    """限流/审计主体：state miirp_client_id，未注入兜底 "anonymous"（§6.7）。"""
    fc = context.fastmcp_context
    if fc is None:
        return "anonymous"
    return (await fc.get_state("miirp_client_id")) or "anonymous"


def _correlation_id(context: MiddlewareContext) -> str:
    """请求链路标识：优先 request_context.request_id，缺失时 uuid 兜底（data_contract §1.2）。"""
    fc = context.fastmcp_context
    rc = getattr(fc, "request_context", None) if fc is not None else None
    rid = getattr(rc, "request_id", None)
    return str(rid) if rid else uuid.uuid4().hex[:12]


def _persist(event: AuditEvent) -> None:
    """审计落库（旁路）：失败仅告警，不阻断业务调用。"""
    try:
        AuditStore.record(event)
    except Exception as exc:  # noqa: BLE001 - 审计落库旁路：吞掉一切存储异常仅告警，不阻断主链路
        logger.warning("audit persist failed: %s", exc)


class AuditMiddleware(Middleware):
    """审计中间件：记录工具调用指标与审计事件（含失败）。"""

    async def on_call_tool(self, context: MiddlewareContext[mt.CallToolRequestParams],
                           call_next: CallNext[mt.CallToolRequestParams, ToolResult]) -> ToolResult:
        start = time.perf_counter()
        params = context.message
        tool = params.name
        correlation_id = _correlation_id(context)
        set_trace_id(correlation_id)   # 贯穿本请求日志与审计（D22）
        client_id = await _client_id(context)
        status = "ok"
        record_call(tool)
        try:
            result = await call_next(context)
            return result
        except Exception as exc:                                   # 捕获全部异常记为 error 事件
            status = f"error:{type(exc).__name__}"
            record_error(tool)
            raise
        finally:
            # P2-3 整改：扣除审批等待时长（ApprovalMiddleware 写入 state
            # miirp_approval_wait_ms），避免最长 120s 的等待污染审计 latency 与直方图。
            wait_ms = 0.0
            fc = context.fastmcp_context
            if fc is not None:
                wait_ms = float((await fc.get_state("miirp_approval_wait_ms")) or 0)
            latency_ms = round(max((time.perf_counter() - start) * 1000 - wait_ms, 0), 2)
            record_latency(latency_ms)
            _persist(AuditEvent(
                correlation_id=correlation_id,
                client_id=client_id,
                tool_name=tool,
                args_summary=summarize(params.arguments),   # D14：值哈希摘要，明文不落库
                status=status,
                latency_ms=latency_ms,
            ))
