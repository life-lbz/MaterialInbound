"""收货单工具（api_contract §2.2；开发文档 §6.8 grn_tools）。

get_grn 角色白名单：iqc-inspector、qty-check-agent；create_grn：grn-operator。
"""
from __future__ import annotations

from fastmcp import FastMCP

from app.services.grn_service import GrnService


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_grn(grn_no: str) -> dict:
        """查询收货单。参数 grn_no: 收货单号。"""
        return await GrnService.get(grn_no)

    @mcp.tool()
    async def create_grn(po_no: str, sku: str, received_qty: int, batch_no: str,
                         carrier: str) -> dict:
        """到货登记生成收货单。参数 po_no: 采购订单号；sku: 物料编码；
        received_qty: 实收数量；batch_no: 收货批次号；carrier: 承运商。"""
        return await GrnService.create(po_no=po_no, sku=sku, received_qty=received_qty,
                                       batch_no=batch_no, carrier=carrier)
