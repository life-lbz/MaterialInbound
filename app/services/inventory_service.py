"""库存台账服务（内存仓储 + 种子数据；生产可换数据库）。

data_contract §3.8：字段 sku/stock_qty/safety_stock/reorder_point/avg_daily_usage/last_90d_usage，
主键 sku；stock_qty < safety_stock 即低库存（单 Agent 场景数据源 §2.6）。
"""
from __future__ import annotations

_STOCK: dict[str, dict] = {}


def _seed() -> None:
    """种子数据（data_contract §4）：
    M-1001 库存 12000 < 安全库存 15000（低于安全线，触发预警）；M-2002 库存 860 > 安全库存 500（正常）。
    """
    _STOCK["M-1001"] = {"sku": "M-1001", "stock_qty": 12000, "safety_stock": 15000,
                        "reorder_point": 18000, "avg_daily_usage": 620, "last_90d_usage": 55800}
    _STOCK["M-2002"] = {"sku": "M-2002", "stock_qty": 860, "safety_stock": 500,
                        "reorder_point": 600, "avg_daily_usage": 12, "last_90d_usage": 1080}


class InventoryService:
    """库存台账：查询、低库存清单、用量统计（单 Agent 安全库存预警数据源）。"""

    @staticmethod
    async def get(sku: str) -> dict:
        """按物料编码查询台账；无记录返回全 0 台账（不抛错，api_contract §2.4）。"""
        return _STOCK.get(sku, {"sku": sku, "stock_qty": 0, "safety_stock": 0,
                                "reorder_point": 0, "avg_daily_usage": 0,
                                "last_90d_usage": 0})

    @staticmethod
    async def list_low_stock() -> list[dict]:
        """列出低于安全库存的物料台账列表（stock_qty < safety_stock）。"""
        return [v for v in _STOCK.values() if v["stock_qty"] < v["safety_stock"]]

    @staticmethod
    async def usage(sku: str, days: int = 90) -> dict:
        """近 N 天用量统计：{sku, days, avg_daily_usage, total_usage}（total_usage 取 last_90d_usage）。"""
        item = _STOCK.get(sku, {})
        return {"sku": sku, "days": days, "avg_daily_usage": item.get("avg_daily_usage", 0),
                "total_usage": item.get("last_90d_usage", 0)}

    @staticmethod
    def restock(sku: str, quantity: int) -> None:
        """入库过账联动（P0-4 整改）：更新库存台账 stock_qty += quantity。

        库存台账仍为内存仓储（生产可换数据库）；入库单本身已落库（inbound_records），
        库存台账的持久化随真实 ERP/库存系统接入时一并迁移。
        """
        item = _STOCK.setdefault(sku, {"sku": sku, "stock_qty": 0, "safety_stock": 0,
                                       "reorder_point": 0, "avg_daily_usage": 0,
                                       "last_90d_usage": 0})
        item["stock_qty"] = int(item.get("stock_qty", 0)) + int(quantity)


_seed()
