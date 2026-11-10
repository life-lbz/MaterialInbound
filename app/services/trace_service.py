"""批次追溯服务（内存仓储 + 种子数据；生产可换数据库）。

data_contract §3.7：字段 batch_no/supplier_batch/production_date/material_lot，
主键 batch_no；trace_complete 派生：supplier_batch 且 production_date 非空（§6.10 get 返回）。
"""
from __future__ import annotations

_TRACES: dict[str, dict] = {}


def _seed() -> None:
    """种子数据（data_contract §4）：
    B20260820011 追溯完整（PASS 路径）；B20260820012 全空 → 追溯链断裂（FAIL 路径）。
    """
    _TRACES["B20260820011"] = {"batch_no": "B20260820011", "supplier_batch": "SB-8801",
                               "production_date": "2026-08-10", "material_lot": "LOT-3102"}
    _TRACES["B20260820012"] = {"batch_no": "B20260820012", "supplier_batch": "",
                               "production_date": "", "material_lot": ""}


class TraceService:
    """批次追溯：批次号 → 供应商生产批 → 物料批次台账（只读）。"""

    @staticmethod
    async def get(batch_no: str) -> dict:
        """按收货批次号查询追溯链（api_contract §2.6）：
        {batch_no, supplier_batch, production_date, material_lot, trace_complete}；
        追溯链断裂时 supplier_batch 为空、trace_complete=False（无记录亦返回空追溯）。"""
        t = _TRACES.get(batch_no, {})
        return {"batch_no": batch_no, "supplier_batch": t.get("supplier_batch", ""),
                "production_date": t.get("production_date", ""),
                "material_lot": t.get("material_lot", ""),
                "trace_complete": bool(t.get("supplier_batch") and t.get("production_date"))}


_seed()
