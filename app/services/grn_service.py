"""收货单服务（内存仓储 + 种子数据；生产可换数据库）。

data_contract §3.2：GRN 字段 grn_no/po_no/sku/received_qty/batch_no/carrier/received_at，
主键 grn_no，新建 = GRN-{uuid4().hex[:10].upper()}，received_at 由服务写入 isoformat。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.exceptions import NotFoundError

_GRNS: dict[str, dict] = {}


def _seed() -> None:
    """种子数据（data_contract §4）：
    GRN-20260820-011 实收 50000（=PO 数量，PASS 路径）；GRN-20260820-012 实收 54000（超收 8%，FAIL 路径）；
    GRN-20260820-021 实收 20000（P0-4 整改新增：e2e 高价值双人审批锚点，关联 PO-20260819003）。
    """
    _GRNS["GRN-20260820-011"] = {"grn_no": "GRN-20260820-011", "po_no": "PO-20260818001",
                                 "sku": "M-1001", "received_qty": 50000,
                                 "batch_no": "B20260820011", "carrier": "SF",
                                 "received_at": "2026-08-20T09:00:00+00:00"}
    _GRNS["GRN-20260820-012"] = {"grn_no": "GRN-20260820-012", "po_no": "PO-20260818001",
                                 "sku": "M-1001", "received_qty": 54000,
                                 "batch_no": "B20260820012", "carrier": "SF",
                                 "received_at": "2026-08-20T09:30:00+00:00"}
    _GRNS["GRN-20260820-021"] = {"grn_no": "GRN-20260820-021", "po_no": "PO-20260819003",
                                 "sku": "M-1001", "received_qty": 20000,
                                 "batch_no": "B20260820021", "carrier": "SF",
                                 "received_at": "2026-08-20T10:00:00+00:00"}


class GrnService:
    """收货单：到货登记生成收货单，供质检（IQC）与入库（IN）引用。"""

    @staticmethod
    async def get(grn_no: str) -> dict:
        """按单号查询收货单；不存在抛 NotFoundError（api_contract §2.2）。"""
        grn = _GRNS.get(grn_no)
        if grn is None:
            raise NotFoundError(f"收货单不存在: {grn_no}")
        return grn

    @staticmethod
    async def create(po_no: str, sku: str, received_qty: int, batch_no: str,
                     carrier: str) -> dict:
        """创建收货单：生成 GRN-{uuid4().hex[:10].upper()}，received_at 由服务写入 isoformat。"""
        grn_no = f"GRN-{uuid.uuid4().hex[:10].upper()}"
        grn = {"grn_no": grn_no, "po_no": po_no, "sku": sku,
               "received_qty": received_qty, "batch_no": batch_no, "carrier": carrier,
               "received_at": datetime.now(timezone.utc).isoformat()}
        _GRNS[grn_no] = grn
        return grn


_seed()
