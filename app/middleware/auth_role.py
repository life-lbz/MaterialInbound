"""鉴权与工具最小可见：未授权工具在 tools/list 阶段即不可见，tools/call 强制白名单校验。

身份解析（api_contract §4/§8）：
- HTTP 模式：请求头 X-MIIRP-Key（auth.api_key_header 配置）→ API Key → 角色
  （实机验证 request_context.request.headers，header 名统一转小写匹配）。
- stdio 模式：`MIIRP_ROLE` 环境变量注入角色（api_contract §8 口径 #5，恒用 MIIRP_ROLE）。
- 握手类方法（initialize/ping/notifications/*）：request_context 尚未就绪，
  不做角色鉴权仅透传；身份校验在 tools/list 与 tools/call 阶段执行（§6.7）。

解析结果写入 fastmcp context state：
- miirp_role：角色
- miirp_client_id：client_id（限流/审计主体；审批人身份读取路径的注入方，api_contract §8 口径 #2）
供 RateLimit/Audit/Approval 中间件与审批工具读取。

双重拦截（api_contract §4/§5、DC-002/D32、RV-M2 P0-1）：
- 最小可见：tools/list 按角色白名单裁剪（未授权工具不可见）。
- 强制校验：tools/call 校验被调用工具 ∈ 角色白名单，未授权抛 PermissionDeniedError
  （HTTP/stdio 直连客户端不经 Agent 编排层，仅裁剪可见性不足以阻断越权调用）。
- 两处白名单数据源一致：PermissionRegistry.tools_for → config roles（单一事实源，不复制两份）。
- 未识别身份（role=None）抛 PermissionError。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from typing import Any, ClassVar

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult

from app.auth.registry import PermissionRegistry
from app.config import AUTH_CFG
from app.exceptions import PermissionDeniedError


class AuthRoleMiddleware(Middleware):
    """鉴权中间件：身份解析 + state 透传 + tools/list 角色裁剪。"""

    # 握手类方法：此时 request_context.request 尚未就绪（实机验证为 None），
    # 不做角色鉴权，仅透传；身份校验在 tools/list 与 tools/call 阶段执行（§6.7）。
    _HANDSHAKE_METHODS: ClassVar[set[str]] = {"initialize", "ping", "notifications/initialized",
                                              "notifications/cancelled", "notifications/progress"}

    @staticmethod
    def resolve_key_role(api_key: str) -> str | None:
        """API Key → 角色（供 /approvals HTTP 路由复用，module_boundaries §5）。"""
        return PermissionRegistry.role_for_api_key(api_key)

    @staticmethod
    def _role_from_context(context: MiddlewareContext) -> str | None:
        """解析调用角色（P2-2 整改：HTTP 请求头优先，env 仅作 stdio/无头兜底）。

        身份注入口径（api_contract §4/§8）：
        - HTTP 模式：请求头 X-MIIRP-Key（auth.api_key_header 配置）→ API Key → 角色；
        - stdio/无请求头：MIIRP_ROLE 环境变量注入（api_contract §8 口径 #5）。
        修复：原实现 env 优先于 header，env 残留可致免 key 越权（P2-2）。
        """
        fc = context.fastmcp_context
        rc = getattr(fc, "request_context", None) if fc is not None else None
        req = getattr(rc, "request", None)
        if req is not None:
            header = str(AUTH_CFG.get("api_key_header", "X-MIIRP-Key")).lower()
            key = req.headers.get(header)
            if key:
                return AuthRoleMiddleware.resolve_key_role(key)
        env_role = os.environ.get("MIIRP_ROLE")
        if env_role:
            return env_role
        return None

    @staticmethod
    def _record_reject(correlation_id: str, client_id: str, tool_name: str,
                       exc_cls: str) -> None:
        """拒绝面审计留痕（P2-1 整改）：越权/身份拒绝事件落审计。

        链首异常不进入 AuditMiddleware（其位于本中间件之后），故在拒绝点直接落库；
        审计落库为旁路：失败仅告警，不阻断鉴权主链路（与 AuditMiddleware 同语义）。
        """
        try:
            from datetime import datetime, timezone

            from app.audit.store import AuditEvent, AuditStore
            AuditStore.record(AuditEvent(
                correlation_id=correlation_id,
                client_id=client_id or "anonymous",
                tool_name=tool_name,
                args_summary="{}",
                status=f"error:{exc_cls}",
                latency_ms=0.0,
                created_at=datetime.now(timezone.utc),
            ))
        except Exception as exc:  # noqa: BLE001 - 审计旁路：落库失败不阻断鉴权
            import logging
            logging.getLogger("miirp.middleware.auth").warning(
                "audit reject persist failed: %s", exc)

    async def on_request(self, context: MiddlewareContext[mt.Request[Any, Any]],
                         call_next: CallNext[mt.Request[Any, Any], Any]) -> Any:
        if str(getattr(context, "method", "")) in AuthRoleMiddleware._HANDSHAKE_METHODS:
            return await call_next(context)
        role = AuthRoleMiddleware._role_from_context(context)
        if role is None:
            # P2-1 整改：身份拒绝事件落审计（拒绝面留痕，审计完整性）
            AuthRoleMiddleware._record_reject(
                uuid.uuid4().hex[:12], "anonymous",
                str(getattr(context, "method", "")), "PermissionError")
            raise PermissionError("missing or invalid client credentials")
        # 状态透传（实机验证 set_state/get_state 跨中间件可见）
        fc = context.fastmcp_context
        if fc is not None:
            await fc.set_state("miirp_role", role)
            await fc.set_state("miirp_client_id", PermissionRegistry.resolve_client_id(role))
        return await call_next(context)

    async def on_call_tool(self, context: MiddlewareContext[mt.CallToolRequestParams],
                           call_next: CallNext[mt.CallToolRequestParams, ToolResult]) -> ToolResult:
        """强制校验：tools/call 按角色白名单拦截越权调用（DC-002/D32，P0-1）。

        - 当前角色取自 state 键 miirp_role（on_request 注入，api_contract §8 口径 #2），
          缺失时以 _role_from_context 兜底重解析，仍未识别则抛 PermissionError。
        - 白名单数据源与 on_list_tools 裁剪一致（PermissionRegistry.tools_for →
          config roles，单一事实源，不复制两份）；白名单不含被调用工具即拒绝，
          抛 PermissionDeniedError（code=PERMISSION_DENIED）。
        - 兜底防线：即使 on_request 被绕过/中间件链局部装配，tools/call 仍强制鉴权。
        """
        params = context.message
        fc = context.fastmcp_context
        role = (await fc.get_state("miirp_role")) if fc is not None else None
        if role is None:
            role = AuthRoleMiddleware._role_from_context(context)
        if role is None:
            # P2-1 整改：身份拒绝事件落审计（拒绝面留痕）
            AuthRoleMiddleware._record_reject(
                uuid.uuid4().hex[:12], "anonymous", params.name, "PermissionError")
            raise PermissionError("missing or invalid client credentials")
        if params.name not in PermissionRegistry.tools_for(role):
            # P2-1 整改：越权调用事件落审计（拒绝面留痕，D14 覆盖拒绝面）
            client_id = PermissionRegistry.resolve_client_id(role)
            AuthRoleMiddleware._record_reject(
                uuid.uuid4().hex[:12], client_id, params.name, "PermissionDeniedError")
            raise PermissionDeniedError(f"角色 {role} 无权调用工具 {params.name}")
        return await call_next(context)

    async def on_list_tools(self, context: MiddlewareContext[mt.ListToolsRequest],
                            call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]]) -> Sequence[Tool]:
        """最小可见：tools/list 按角色白名单裁剪（未授权工具不可见，§6.7）。"""
        tools = await call_next(context)
        role = AuthRoleMiddleware._role_from_context(context)
        allowed = PermissionRegistry.tools_for(role) if role else set()
        return [t for t in tools if t.name in allowed]
