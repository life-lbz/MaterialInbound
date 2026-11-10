"""供应商档案与报价工具（api_contract §2.5；开发文档 §6.8 supplier_tools）。

角色白名单：get_supplier_profile（compliance-officer、supplier-check-agent）；
get_supplier_quote（buyer、price-check-agent）。
"""
from __future__ import annotations

from fastmcp import FastMCP

from app.services.supplier_service import SupplierService


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_supplier_profile(supplier_id: str) -> dict:
        """查询供应商资质与绩效档案。参数 supplier_id: 供应商编码。"""
        return await SupplierService.profile(supplier_id)

    @mcp.tool()
    async def get_supplier_quote(supplier_id: str, sku: str) -> dict:
        """查询供应商近 90 天报价区间与最新报价。参数 supplier_id: 供应商编码；sku: 物料编码。"""
        return await SupplierService.quote(supplier_id, sku)
