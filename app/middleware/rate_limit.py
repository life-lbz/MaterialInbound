"""限流中间件：按 client_id 滑动窗口，超出阈值抛 RateLimitError（D11）。

- 主体：client_id（AuthRoleMiddleware 注入的 state 键 miirp_client_id），
  未鉴权/握手请求兜底 "anonymous"（§6.7 匿名口径）。
- 滑动窗口：窗口容量 = rate_limit.qps × rate_limit.window_seconds（default 20×60=1200 次/窗），
  过期时间戳（now - ts > window_seconds）从队首弹出后计数。
- 阈值与窗口参数来自配置（config/default.yaml rate_limit.*，§6.1），非硬编码。
- 命中后抛 RateLimitError（code=RATE_LIMITED，§6.4），由 ErrorHandlingMiddleware 转工具错误。
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from app.config import RATE_LIMIT_CFG
from app.exceptions import RateLimitError

# 每 client_id 窗口容量（qps × window_seconds）与窗口时长（秒），来自配置（§6.1）
_qps = float(RATE_LIMIT_CFG.get("qps", 20))
_window = float(RATE_LIMIT_CFG.get("window_seconds", 60))
_lock = Lock()
_hits: dict[str, deque] = defaultdict(deque)


def _record_reject(client_id: str) -> None:
    """限流拒绝审计留痕（P2-1 整改）：拒绝面事件落审计（旁路，失败不阻断）。"""
    try:
        from app.audit.store import AuditEvent, AuditStore
        AuditStore.record(AuditEvent(
            correlation_id=uuid.uuid4().hex[:12],
            client_id=client_id,
            tool_name="rate_limit",
            args_summary="{}",
            status="error:RateLimitError",
            latency_ms=0.0,
            created_at=datetime.now(timezone.utc),
        ))
    except Exception as exc:  # noqa: BLE001 - 审计旁路：落库失败不阻断限流
        import logging
        logging.getLogger("miirp.middleware.rate_limit").warning(
            "audit rate-limit persist failed: %s", exc)


class RateLimitMiddleware(Middleware):
    """滑动窗口限流：按 client_id 计数，窗口内请求数超容量即拒绝。"""

    async def on_request(self, context: MiddlewareContext[mt.Request[Any, Any]],
                         call_next: CallNext[mt.Request[Any, Any], Any]) -> Any:
        fc = context.fastmcp_context
        client_id = (await fc.get_state("miirp_client_id")) if fc is not None else None
        client_id = client_id or "anonymous"
        now = time.monotonic()
        with _lock:
            dq = _hits[client_id]
            while dq and now - dq[0] > _window:
                dq.popleft()
            if len(dq) >= _qps * _window:
                _record_reject(client_id)   # P2-1 整改：限流拒绝审计留痕
                raise RateLimitError(f"rate limit exceeded for {client_id}")
            dq.append(now)
        return await call_next(context)
