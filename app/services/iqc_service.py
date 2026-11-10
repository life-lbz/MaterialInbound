"""质检报告服务（内存仓储 + 种子数据；生产可换数据库）。

data_contract §3.3：IQC 字段 iqc_no/grn_no/sku/sample_qty/defect_qty/verdict，
主键 iqc_no，新建 = IQC-{uuid4().hex[:10].upper()}，
verdict 状态机：`"pass" if defect_qty == 0 else "fail"`。
"""
from __future__ import annotations

import uuid

from app.exceptions import NotFoundError

_IQCS: dict[str, dict] = {}


def _seed() -> None:
    """种子数据（data_contract §4）：
    IQC-20260820-011 不良 0 → pass（PASS 路径）；IQC-20260820-012 不良 2 → fail（FAIL 路径）；
    IQC-20260820-021 不良 0 → pass（P0-4 整改新增：e2e 高价值双人审批锚点，关联 GRN-20260820-021）。
    """
    _IQCS["IQC-20260820-011"] = {"iqc_no": "IQC-20260820-011", "grn_no": "GRN-20260820-011",
                                 "sku": "M-1001", "sample_qty": 320, "defect_qty": 0,
                                 "verdict": "pass"}
    _IQCS["IQC-20260820-012"] = {"iqc_no": "IQC-20260820-012", "grn_no": "GRN-20260820-012",
                                 "sku": "M-1001", "sample_qty": 320, "defect_qty": 2,
                                 "verdict": "fail"}
    _IQCS["IQC-20260820-021"] = {"iqc_no": "IQC-20260820-021", "grn_no": "GRN-20260820-021",
                                 "sku": "M-1001", "sample_qty": 200, "defect_qty": 0,
                                 "verdict": "pass"}


class IqcService:
    """来料检验（IQC）：抽样质检报告，verdict 由不良数派生。"""

    @staticmethod
    async def get(iqc_no: str) -> dict:
        """按报告号查询质检报告；不存在抛 NotFoundError（api_contract §2.3）。"""
        iqc = _IQCS.get(iqc_no)
        if iqc is None:
            raise NotFoundError(f"质检报告不存在: {iqc_no}")
        return iqc

    @staticmethod
    async def create(grn_no: str, sku: str, sample_qty: int, defect_qty: int) -> dict:
        """创建质检报告：生成 IQC-{uuid4().hex[:10].upper()}；defect_qty==0 判 pass，否则 fail。"""
        iqc_no = f"IQC-{uuid.uuid4().hex[:10].upper()}"
        iqc = {"iqc_no": iqc_no, "grn_no": grn_no, "sku": sku,
               "sample_qty": sample_qty, "defect_qty": defect_qty,
               "verdict": "pass" if defect_qty == 0 else "fail"}
        _IQCS[iqc_no] = iqc
        return iqc


_seed()
