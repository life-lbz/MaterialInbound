"""库存台账工具（api_contract §2.4；开发文档 §6.8 inventory_tools）。

角色白名单：get_inventory（warehouse-manager、buyer、inventory-agent）；
list_low_stock_materials（warehouse-manager、inventory-agent）；
get_stock_usage（inventory-agent）。
"""
from __future__ import annotations

from fastmcp import FastMCP

from app.services.inventory_service import InventoryService


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_inventory(sku: str) -> dict:
        """查询物料库存与安全线。参数 sku: 物料编码。"""
        return await InventoryService.get(sku)

    @mcp.tool()
    async def list_low_stock_materials() -> list[dict]:
        """列出低于安全库存的物料清单。"""
        return await InventoryService.list_low_stock()

    @mcp.tool()
    async def get_stock_usage(sku: str, days: int = 90) -> dict:
        """查询近 N 天用量统计。参数 sku: 物料编码；days: 天数（默认 90）。"""
        return await InventoryService.usage(sku, days)
