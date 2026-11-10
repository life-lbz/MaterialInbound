"""质检报告工具（api_contract §2.3；开发文档 §6.8 iqc_tools）。

create_iqc_report 角色白名单：iqc-inspector；get_iqc 不在任何角色白名单（§5 口径 #1）。
"""
from __future__ import annotations

from fastmcp import FastMCP

from app.services.iqc_service import IqcService


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_iqc(iqc_no: str) -> dict:
        """查询质检报告。参数 iqc_no: 质检报告号。"""
        return await IqcService.get(iqc_no)

    @mcp.tool()
    async def create_iqc_report(grn_no: str, sku: str, sample_qty: int,
                                defect_qty: int) -> dict:
        """生成来料检验报告。参数 grn_no: 收货单号；sku: 物料编码；
        sample_qty: 抽样数；defect_qty: 不良数（>0 判 fail）。"""
        return await IqcService.create(grn_no=grn_no, sku=sku,
                                       sample_qty=sample_qty, defect_qty=defect_qty)
