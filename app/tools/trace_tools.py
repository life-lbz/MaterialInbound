"""批次追溯工具（只读）（api_contract §2.6；开发文档 §6.8 trace_tools）。

角色白名单：compliance-officer、trace-check-agent。
追溯链：批次号 → 供应商生产批 → 物料批次台账；断裂时 supplier_batch 为空、trace_complete=False。
"""
from __future__ import annotations

from fastmcp import FastMCP

from app.services.trace_service import TraceService


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_trace_batch(batch_no: str) -> dict:
        """查询批次追溯链。参数 batch_no: 收货批次号。
        返回供应商生产批、生产日期、物料批次；追溯链断裂时 supplier_batch 为空。"""
        return await TraceService.get(batch_no)
