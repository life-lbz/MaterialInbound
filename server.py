"""MCP Server 入口：装配中间件、注册工具、挂载 /healthz /metrics /approvals。

部署形态（fastmcp 3.4.7 实机验证，见开发文档 §3.4）：
- 开发：python server.py --transport stdio
- 生产：python server.py --transport streamable-http
  内部通过 custom_route 注册健康检查/指标/审批查询路由，
  经 mcp.http_app() 得到 ASGI app 交给 uvicorn 承载（容器内执行）。

M1 过渡装配方案（module_boundaries §5.1）：
- 治理中间件（app/middleware/*，A3 所有权）与工具模块（app/tools/*，A4 所有权）均为 M2 交付物；
  M1 期采用 try-import 延迟加载，模块缺失时占位注册（无中间件/无工具），保证骨架可启动；
  M2 交付后 try-import 命中即真实注册，本文件无需再改动。
- 装载顺序固定：AuthRole→RateLimit→Audit→Approval→Logging→Timing→ErrorHandling（§4 红线）。
"""
from __future__ import annotations

import argparse
import importlib
import logging

from fastapi import Request, Response
from fastmcp import FastMCP
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

from app.config import APPROVE_ROLES, AUTH_CFG, LOG_CFG, METRICS_AUTH, SERVER_CFG
from app.db.migrate import migrate
from app.logging_setup import setup_logging
from app.middleware.error_handling import BusinessErrorHandlingMiddleware
from app.monitoring import healthz as healthz_fn
from app.monitoring import metrics_text

# 请求体大小限制由 Nginx client_max_body_size 在网关层执行（见 nginx.conf），
# 应用侧 max_body_bytes 作为配置项留存于 config/default.yaml。

logger = logging.getLogger("miirp.server")

# ---- M2 治理中间件（A3 所有权）：try-import 延迟加载，M1 期缺失时占位 None ----
# 装载顺序固定：AuthRole→RateLimit→Audit→Approval（module_boundaries §4 红线，禁止 A3/A4 改写）
_AuthRoleMiddleware = None
try:
    from app.middleware.auth_role import AuthRoleMiddleware as _AuthRoleMiddleware
except ImportError:  # M1 过渡期：A3 未交付，占位（§5.1）
    logger.info("中间件 AuthRoleMiddleware 未交付（M1 占位，M2 交付后自动挂载）")

_RateLimitMiddleware = None
try:
    from app.middleware.rate_limit import RateLimitMiddleware as _RateLimitMiddleware
except ImportError:
    logger.info("中间件 RateLimitMiddleware 未交付（M1 占位，M2 交付后自动挂载）")

_AuditMiddleware = None
try:
    from app.middleware.audit import AuditMiddleware as _AuditMiddleware
except ImportError:
    logger.info("中间件 AuditMiddleware 未交付（M1 占位，M2 交付后自动挂载）")

_ApprovalMiddleware = None
try:
    from app.middleware.approval import ApprovalMiddleware as _ApprovalMiddleware
except ImportError:
    logger.info("中间件 ApprovalMiddleware 未交付（M1 占位，M2 交付后自动挂载）")

# ---- M2 工具模块（A4 所有权）：9 个模块统一 register(mcp: FastMCP) -> None ----
_TOOL_MODULES = (
    "po_tools", "grn_tools", "iqc_tools", "inventory_tools",
    "supplier_tools", "trace_tools", "inbound_tools",
    "approval_tools", "notify_tools",
)


def _register_tools(mcp: FastMCP) -> int:
    """工具模块 try-import 延迟注册（§5.1）：M2 交付前缺失跳过（占位），交付后 register(mcp) 真实注册。"""
    registered = 0
    for mod_name in _TOOL_MODULES:
        try:
            mod = importlib.import_module(f"app.tools.{mod_name}")
            mod.register(mcp)
            registered += 1
        except ImportError:  # M1 过渡期：A4 未交付，占位（§5.1）
            logger.info("工具模块 app.tools.%s 未交付（M1 占位，M2 交付后自动注册）", mod_name)
    return registered


def build_app() -> FastMCP:
    """构建 FastMCP 实例：启动迁移 + 装配中间件（顺序固定）+ 注册工具。"""
    migrate()   # 启动时执行幂等迁移

    middleware_chain: list = []
    for cls in (_AuthRoleMiddleware, _RateLimitMiddleware, _AuditMiddleware, _ApprovalMiddleware):
        if cls is not None:
            middleware_chain.append(cls())          # M2 交付后真实注册（类名可无参实例化）
    middleware_chain.extend([
        LoggingMiddleware(),
        TimingMiddleware(),
        BusinessErrorHandlingMiddleware(),   # P2-4 整改：业务错误码透传（替代内置 Generic Error 归一）
    ])

    mcp = FastMCP(
        name="miirp",
        version=str(SERVER_CFG.get("version", "3.1.0")),   # D31：与 config/server.version 统一
        instructions="物料入库智能审查平台工具层",
        middleware=middleware_chain,
    )
    _register_tools(mcp)
    return mcp


def mount_http(mcp: FastMCP) -> FastMCP:
    """注册 HTTP 附加路由（实机验证：custom_route 与 /mcp 共存）。"""

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:
        import json
        return Response(json.dumps(healthz_fn(), ensure_ascii=False), media_type="application/json")

    @mcp.custom_route("/metrics", methods=["GET"])
    async def metrics(request: Request) -> Response:
        """指标端点（P3 整改）：production 配置 metrics.auth=true 时要求 X-MIIRP-Key 鉴权。"""
        import json
        if METRICS_AUTH:
            header = str(AUTH_CFG.get("api_key_header", "X-MIIRP-Key")).lower()
            key = request.headers.get(header) or ""
            if _AuthRoleMiddleware is not None \
                    and _AuthRoleMiddleware.resolve_key_role(key) is None:
                return Response(json.dumps({"error": "forbidden"}, ensure_ascii=False),
                                status_code=403, media_type="application/json")
        return Response(metrics_text(), media_type="text/plain; version=0.0.4")

    @mcp.custom_route("/approvals", methods=["GET"])
    async def approvals(request: Request) -> Response:
        """审批待办查询：M2 后由 AuthRoleMiddleware.resolve_key_role 鉴权（X-MIIRP-Key 头）；
        M1 过渡期中间件/审批服务未交付时占位返回空列表（module_boundaries §5.1）。"""
        import json
        if _AuthRoleMiddleware is not None:
            key = request.headers.get("X-MIIRP-Key") or ""
            role = _AuthRoleMiddleware.resolve_key_role(key)
            # P2-5 整改：审批角色读配置 APPROVE_ROLES（单一事实源），
            # 替换原硬编码 ("warehouse-manager", "compliance-officer") 双源分叉。
            allowed = role in APPROVE_ROLES
            if not allowed:
                return Response(json.dumps({"error": "forbidden"}, ensure_ascii=False), status_code=403,
                                media_type="application/json")
        try:
            from app.approval.service import ApprovalService
            status = request.query_params.get("status", "pending")
            items = await ApprovalService.list_by_status(status)
            return Response(json.dumps(items, ensure_ascii=False), media_type="application/json")
        except ImportError:  # M1 过渡期：审批服务未交付，占位空列表（§5.1）
            return Response(json.dumps([], ensure_ascii=False), media_type="application/json")

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "streamable-http"],
                        default=SERVER_CFG.get("transport", "stdio"))
    args = parser.parse_args()

    # 日志级别/JSON 开关取 logging 段配置（default.yaml：level=INFO, json=true）
    setup_logging(level=str(LOG_CFG.get("level", "INFO")), json_mode=bool(LOG_CFG.get("json", True)))
    mcp = mount_http(build_app())

    if args.transport == "stdio":
        logger.info("starting MCP server (stdio)")
        mcp.run(transport="stdio")
    else:
        import uvicorn
        host = SERVER_CFG.get("host", "0.0.0.0")
        port = int(SERVER_CFG.get("port", 8000))
        logger.info("starting MCP server (streamable-http) on %s:%s", host, port)
        # 实机验证：http_app 返回 StarletteWithLifespan，路由含 /mcp /healthz /metrics
        app = mcp.http_app(path="/mcp", transport="streamable-http")
        uvicorn.run(app, host=host, port=port, log_level="warning",
                    proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
