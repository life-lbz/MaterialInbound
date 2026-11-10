"""请购单/采购订单工具（api_contract §2.1；开发文档 §6.8 po_tools）。

get_po 角色白名单：grn-operator、qty-check-agent；create_po 不在任何角色白名单（§5 口径 #1）。
"""
from __future__ import annotations

from fastmcp import FastMCP

from app.services.order_service import OrderService


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_po(po_no: str) -> dict:
        """查询采购订单：物料、数量、单价、币种、供应商。参数 po_no: 采购订单号。"""
        return await OrderService.get(po_no)

    @mcp.tool()
    async def create_po(pr_no: str, sku: str, quantity: int, unit_price: float,
                        currency: str, supplier_id: str) -> dict:
        """创建采购订单。参数 pr_no: 请购单号；sku: 物料编码；quantity: 数量；
        unit_price: 单价；currency: 币种；supplier_id: 供应商编码。"""
        return await OrderService.create(pr_no=pr_no, sku=sku, quantity=quantity,
                                         unit_price=unit_price, currency=currency,
                                         supplier_id=supplier_id)
