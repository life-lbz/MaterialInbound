"""入库单服务：审批通过后由工具执行，写 IN 台账（DB 落库，P0-4 整改）。

data_contract §3.4（v3.0 选项 B，P0-4 整改）：
- 字段 in_no/grn_no/iqc_no/sku/inbound_qty/unit_price/currency/supplier_id/
  approval_request_id/status/reviewed_by，主键 in_no，新建 = IN-{uuid4().hex[:10].upper()}；
- IN 落库恒 status="approved"、reviewed_by="approval-flow"（pending/rejected 由审批单承载，§5 口径 #1）；
- 幂等（D1 升级回归 + P0-4 落库）：inbound_records.approval_request_id 唯一索引，
  同一 approval_request_id 重复 create 返回同一 in_no（跨进程/重启仍生效，DB 语义）。

P1-2 整改（引用完整性校验）：入库创建前校验
- GRN / IQC / PO 单据存在（grn_service / iqc_service / order_service）；
- IQC verdict == "pass"（质检未通过禁止过账）；
- 供应商与 PO 一致、物料（sku）与 PO/GRN 一致、单价与 PO 一致、
  入库数量与 GRN 实收数量一致（"实收 vs PO"口径，§2.4）。

P0-4 库存联动：过账成功后更新库存台账（InventoryService.restock）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.db.connection import connection
from app.exceptions import BusinessError, RiskRejectedError

_FLOAT_TOLERANCE = 1e-9


class InboundService:
    """入库单：以审批单号为幂等键创建（同一审批单重复执行返回同一单据）。"""

    @staticmethod
    async def _get_by_approval(approval_request_id: str) -> dict | None:
        """按审批单号查已建入库单（幂等命中，DB 查询跨进程可靠）。"""
        with connection() as c:
            rows = c.query("SELECT * FROM inbound_records WHERE approval_request_id=?",
                           (approval_request_id,))
        return rows[0] if rows else None

    @staticmethod
    async def create(grn_no: str, iqc_no: str, sku: str, quantity: int,
                     unit_price: float, currency: str, supplier_id: str,
                     approval_request_id: str | None = None) -> dict:
        """创建入库单。

        流程（P0-4/P1-2 整改）：
        1. 幂等：approval_request_id 已存在 → 直接返回已建单据（DB 唯一索引兜底）；
        2. 引用完整性校验：GRN/IQC/PO 存在且一致、IQC 通过、实收数量一致；
        3. 落库 inbound_records（approval_request_id 唯一索引防并发重复过账）；
        4. 库存联动：更新库存台账。
        """
        # 1. 幂等返回（选项 B，D1 升级回归；落库版跨进程/重启有效）
        if approval_request_id:
            existing = await InboundService._get_by_approval(approval_request_id)
            if existing:
                return existing

        # 2. 引用完整性校验（P1-2 整改）
        grn = await InboundService._require_grn(grn_no)
        await InboundService._require_iqc(iqc_no)
        po = await InboundService._require_po(grn.get("po_no") or "")
        InboundService._check_consistency(grn, po, iqc_no, sku, quantity,
                                          unit_price, supplier_id)

        # 3. 落库（approval_request_id 唯一索引防并发重复过账）
        in_no = f"IN-{uuid.uuid4().hex[:10].upper()}"
        record = {"in_no": in_no, "grn_no": grn_no, "iqc_no": iqc_no, "sku": sku,
                  "inbound_qty": int(quantity), "unit_price": float(unit_price),
                  "currency": currency, "supplier_id": supplier_id,
                  "approval_request_id": approval_request_id,
                  "status": "approved", "reviewed_by": "approval-flow",
                  "created_at": datetime.now(timezone.utc).isoformat()}
        with connection() as c:
            c.execute(
                "INSERT INTO inbound_records (in_no, grn_no, iqc_no, sku, inbound_qty,"
                " unit_price, currency, supplier_id, approval_request_id, status,"
                " reviewed_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (record["in_no"], record["grn_no"], record["iqc_no"], record["sku"],
                 record["inbound_qty"], record["unit_price"], record["currency"],
                 record["supplier_id"], record["approval_request_id"], record["status"],
                 record["reviewed_by"], record["created_at"]),
            )

        # 4. 库存联动（P0-4 整改：过账更新库存台账）
        from app.services.inventory_service import InventoryService
        InventoryService.restock(sku, int(quantity))

        return record

    @staticmethod
    async def _require_grn(grn_no: str) -> dict:
        """GRN 必须存在（引用完整性，P1-2）；不存在抛 NotFoundError。"""
        from app.services.grn_service import GrnService
        return await GrnService.get(grn_no)

    @staticmethod
    async def _require_iqc(iqc_no: str) -> dict:
        """IQC 必须存在且 verdict=pass（质检未通过禁止过账，P1-2）。"""
        from app.services.iqc_service import IqcService
        iqc = await IqcService.get(iqc_no)
        if iqc.get("verdict") != "pass":
            raise RiskRejectedError(
                f"质检报告 {iqc_no} 未通过（verdict={iqc.get('verdict')}），禁止入库")
        return iqc

    @staticmethod
    async def _require_po(po_no: str) -> dict:
        """PO 必须存在（引用完整性，P1-2）；不存在抛 NotFoundError。"""
        from app.services.order_service import OrderService
        return await OrderService.get(po_no)

    @staticmethod
    def _check_consistency(grn: dict, po: dict, iqc_no: str, sku: str,
                           quantity: int, unit_price: float, supplier_id: str) -> None:
        """业务一致性校验（P1-2 整改，全部不匹配即拒绝入库）。"""
        po_no = po.get("po_no", "")
        if grn.get("po_no") != po_no:
            raise BusinessError(f"GRN {grn.get('grn_no')} 与 PO {po_no} 不匹配（收货单归属订单不符）")
        if sku != po.get("sku") or sku != grn.get("sku"):
            raise BusinessError(
                f"物料 {sku} 与 PO {po_no}/{grn.get('grn_no')} 不一致（应 "
                f"{po.get('sku')}/{grn.get('sku')}）")
        if supplier_id != po.get("supplier_id"):
            raise BusinessError(
                f"供应商 {supplier_id} 与 PO {po_no} 不一致（应 {po.get('supplier_id')}）")
        if abs(float(unit_price) - float(po.get("unit_price", 0.0))) > _FLOAT_TOLERANCE:
            raise BusinessError(
                f"单价 {unit_price} 与 PO {po_no} 不一致（应 {po.get('unit_price')}）")
        if int(quantity) != int(grn.get("received_qty", 0)):
            raise BusinessError(
                f"入库数量 {quantity} 与 GRN {grn.get('grn_no')} 实收数量 "
                f"{grn.get('received_qty')} 不一致（实收口径，§2.4）")
