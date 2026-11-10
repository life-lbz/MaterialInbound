"""种子数据汇总与校验（data_contract §4；开发文档 §6.10）。

services 各模块在 import 时即加载种子数据（模块级字典 _ORDERS/_GRNS/_IQCS/
_STOCK/_SUPPLIERS/_QUOTES/_TRACES）；本模块提供 verify() 统一校验，
确保 PASS/FAIL 两类演示路径（能力说明 §10 的 GRN-20260820-011/012）与
评测用例 c01-c04 的数据源齐全可查。
生产环境接入真实 ERP 时，将对应服务的 _ORDERS/_STOCK 等替换为数据库查询即可，
工具层与 Agent 层无需改动——这是"可替换为真实系统"的接口设计目标。
"""
from __future__ import annotations

from app.services import (
    grn_service,
    inventory_service,
    iqc_service,
    order_service,
    supplier_service,
    trace_service,
)

# PASS/FAIL 双路径锚点（能力说明 §10）：GRN-20260820-011 通过 / GRN-20260820-012 拒绝
_PASS_PATH = {
    "po": "PO-20260818001", "grn": "GRN-20260820-011", "iqc": "IQC-20260820-011",
    "batch": "B20260820011", "supplier": "SUP-02", "sku": "M-1001",
}
_FAIL_PATH = {
    "po": "PO-20260818001", "grn": "GRN-20260820-012", "iqc": "IQC-20260820-012",
    "batch": "B20260820012", "supplier": "SUP-01", "sku": "M-1001",
}


def verify() -> dict:
    """校验种子数据完整性，返回 {pass_path, fail_path, errors}。

    - pass_path/fail_path：双路径锚点单据是否可查（True/False）；
    - errors：缺失或与契约不符的记录说明列表（空 = 校验通过）。
    """
    errors: list[str] = []

    def _check(store: dict, key: str, label: str) -> bool:
        if store.get(key) is None:
            errors.append(f"种子缺失: {label} {key}")
            return False
        return True

    # PASS 路径可查
    ok_pass = all([
        _check(order_service._ORDERS, _PASS_PATH["po"], "PO"),
        _check(grn_service._GRNS, _PASS_PATH["grn"], "GRN"),
        _check(iqc_service._IQCS, _PASS_PATH["iqc"], "IQC"),
        _check(trace_service._TRACES, _PASS_PATH["batch"], "追溯批次"),
        _check(supplier_service._SUPPLIERS, _PASS_PATH["supplier"], "供应商"),
    ])
    # FAIL 路径可查
    ok_fail = all([
        _check(order_service._ORDERS, _FAIL_PATH["po"], "PO"),
        _check(grn_service._GRNS, _FAIL_PATH["grn"], "GRN"),
        _check(iqc_service._IQCS, _FAIL_PATH["iqc"], "IQC"),
        _check(trace_service._TRACES, _FAIL_PATH["batch"], "追溯批次"),
        _check(supplier_service._SUPPLIERS, _FAIL_PATH["supplier"], "供应商"),
    ])

    # 关键业务量断言（与 data_contract §4 一致，防种子漂移）
    grn_fail = grn_service._GRNS.get("GRN-20260820-012", {})
    if grn_fail.get("received_qty") != 54000:
        errors.append("FAIL 路径 GRN-20260820-012 实收数应为 54000（超收 8%）")
    iqc_fail = iqc_service._IQCS.get("IQC-20260820-012", {})
    if iqc_fail.get("verdict") != "fail":
        errors.append("FAIL 路径 IQC-20260820-012 verdict 应为 fail")
    if inventory_service._STOCK.get("M-1001", {}).get("stock_qty", 0) >= \
            inventory_service._STOCK.get("M-1001", {}).get("safety_stock", 0):
        errors.append("库存种子 M-1001 应低于安全库存（触发低库存预警）")

    return {"pass_path": ok_pass, "fail_path": ok_fail, "errors": errors}
