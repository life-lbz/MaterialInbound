"""入库单工具（写 · 需审批）（api_contract §2.7；开发文档 §6.8 inbound_tools）。

v3.0（选项 B）：工具从调用上下文读取审批单号（由 ApprovalMiddleware 在审批通过后
注入 state 键 miirp_approval_request_id），随业务单据落库，实现"入库单 ↔ 审批单"
关联与幂等（同一审批单重复执行返回同一 in_no）。
v2.0 关键变更：本工具只负责业务执行，不再创建审批单（D1 回归）；
审批单统一由 ApprovalMiddleware 创建（唯一入口），审批通过后才执行本工具。

角色白名单：warehouse-manager、compliance-officer、review-agent。
"""
from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.context import Context

from app.services.inbound_service import InboundService


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def create_inbound_record(grn_no: str, iqc_no: str, sku: str,
                                    quantity: int, unit_price: float,
                                    currency: str, supplier_id: str,
                                    ctx: Context | None = None) -> dict:
        """创建入库单（写操作，自动进入人工审批流程；高价值单据双人审批）。
        参数 grn_no: 收货单号；iqc_no: 质检报告号；sku: 物料编码；
        quantity: 入库数量；unit_price: 单价；currency: 币种；supplier_id: 供应商编码。
        审批通过后本工具被执行，返回入库单号与关联的审批单号（幂等）。"""
        approval_request_id = None
        if ctx is not None:
            approval_request_id = await ctx.get_state("miirp_approval_request_id")
        return await InboundService.create(grn_no=grn_no, iqc_no=iqc_no, sku=sku,
                                           quantity=quantity, unit_price=unit_price,
                                           currency=currency, supplier_id=supplier_id,
                                           approval_request_id=approval_request_id)
