"""采购订单服务（内存仓储 + 种子数据；生产可换数据库）。

data_contract §3.1：PO 字段 po_no/pr_no/sku/quantity/unit_price/currency/supplier_id/status，
主键 po_no，新建 = PO-{uuid4().hex[:8].upper()}，status 恒 "open"。
"""
from __future__ import annotations

import uuid

from app.exceptions import NotFoundError

_ORDERS: dict[str, dict] = {}


def _seed() -> None:
    """种子数据（data_contract §4）：
    - PO-20260818001 归属 SUP-02（P1-6 整改：与 PASS 路径 GRN-20260820-011 的
      supplier_id=SUP-02 一致，消除"通过依赖与 PO 不一致的供应商档案"矛盾）；
    - PO-20260819002（SUP-02/M-2002/CNY 18.5，国产物料补充用例）；
    - PO-20260819003（SUP-02/M-1001/CNY 10.0，e2e 高价值双人审批锚点，
      关联 GRN-20260820-021/IQC-20260820-021，金额 10.0×20000=200000≥10 万阈值）。
    """
    _ORDERS["PO-20260818001"] = {
        "po_no": "PO-20260818001", "pr_no": "PR-20260815001", "sku": "M-1001",
        "quantity": 50000, "unit_price": 0.0071, "currency": "USD",
        "supplier_id": "SUP-02", "status": "open",
    }
    _ORDERS["PO-20260819002"] = {
        "po_no": "PO-20260819002", "pr_no": "PR-20260815002", "sku": "M-2002",
        "quantity": 2000, "unit_price": 18.5, "currency": "CNY",
        "supplier_id": "SUP-02", "status": "open",
    }
    _ORDERS["PO-20260819003"] = {
        "po_no": "PO-20260819003", "pr_no": "PR-20260815003", "sku": "M-1001",
        "quantity": 20000, "unit_price": 10.0, "currency": "CNY",
        "supplier_id": "SUP-02", "status": "open",
    }


class OrderService:
    """采购订单：查询与创建（模拟 ERP PO 台账）。"""

    @staticmethod
    async def get(po_no: str) -> dict:
        """按单号查询采购订单；不存在抛 NotFoundError（api_contract §2.1）。"""
        order = _ORDERS.get(po_no)
        if order is None:
            raise NotFoundError(f"采购订单不存在: {po_no}")
        return order

    @staticmethod
    async def create(pr_no: str, sku: str, quantity: int, unit_price: float,
                     currency: str, supplier_id: str) -> dict:
        """创建采购订单：生成 PO-{uuid4().hex[:8].upper()}，status="open"。"""
        po_no = f"PO-{uuid.uuid4().hex[:8].upper()}"
        order = {"po_no": po_no, "pr_no": pr_no, "sku": sku, "quantity": quantity,
                 "unit_price": unit_price, "currency": currency,
                 "supplier_id": supplier_id, "status": "open"}
        _ORDERS[po_no] = order
        return order


_seed()
