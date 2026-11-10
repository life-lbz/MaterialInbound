"""审批工具：审批人跨进程触发审批决策，并查询待办（api_contract §2.8；修复 D5）。

v2.0 第二轮审核修复（D26）：approve_inbound_request 的审批人身份取自调用上下文，
不信任客户端传入（防止身份伪造导致审批留痕不可追溯）。
审批人身份唯一读取路径（api_contract §8 口径 #2）：
优先 ctx.get_state("miirp_client_id")（AuthRoleMiddleware 注入的 state 键），
兜底 ctx.client_id（fastmcp 3.4.7 Context 属性），两者皆无兜底 "unknown"。

角色白名单：warehouse-manager、compliance-officer。
"""
from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.context import Context

from app.approval.service import ApprovalService


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def approve_inbound_request(request_id: str, approved: bool, reason: str = "",
                                      ctx: Context | None = None) -> dict:
        """审批入库申请。参数 request_id: 审批单号；approved: 是否通过；
        reason: 审批意见。高价值单据需两名审批人依次通过（任一拒绝即整体拒绝）。
        审批人身份取自调用上下文（state miirp_client_id 优先、ctx.client_id 兜底），
        调用方不可伪造（D26）。"""
        reviewer = "unknown"
        if ctx is not None:
            reviewer = (
                await ctx.get_state("miirp_client_id")
            ) or getattr(ctx, "client_id", None) or "unknown"
        return await ApprovalService.decide(request_id=request_id, approved=approved,
                                            reviewer=reviewer, reason=reason)

    @mcp.tool()
    async def list_pending_approvals() -> list[dict]:
        """列出当前待审批的入库申请（含金额与双人审批标记）。"""
        return await ApprovalService.list_by_status("pending")
