"""入库幂等与引用完整性（P0-4/P1-2 整改：入库单从进程内存态落库）。

开发文档 §8.2 语义、data_contract §3.4、D1 升级回归（P0-4 整改后）：
- 同一 approval_request_id 重复 create → 同一 in_no（DB 唯一索引 + 幂等查询，
  跨进程/重启仍生效）；
- 不同 approval_request_id → 不同 in_no；
- 引用完整性（P1-2 整改）：GRN/IQC/PO 不存在、IQC fail、供应商/物料/单价/数量
  与 PO/GRN 不一致 → 拒绝入库；
- 库存联动（P0-4 整改）：过账成功后库存台账 stock_qty 增加。
"""
from __future__ import annotations

import pytest

from app.exceptions import BusinessError, NotFoundError, RiskRejectedError
from app.services.inbound_service import InboundService
from app.services.inventory_service import InventoryService

# 引用种子锚点：GRN-20260820-011（PO-20260818001，SUP-02，0.0071 USD，实收 50000）
_GRN, _IQC, _SKU, _QTY = "GRN-20260820-011", "IQC-20260820-011", "M-1001", 50000
_PRICE, _CUR, _SUP = 0.0071, "USD", "SUP-02"


@pytest.fixture(autouse=True)
def _db(isolated_sqlite):
    """入库单落库需真实数据库（P0-4 整改），全部用例走临时 SQLite。"""
    return isolated_sqlite


@pytest.mark.asyncio
async def test_same_approval_id_returns_same_inbound():
    """幂等：同一审批单 → 同一入库单（选项 B，D1 升级回归；落库版跨进程可靠）。"""
    r1 = await InboundService.create(_GRN, _IQC, _SKU, _QTY, _PRICE, _CUR, _SUP,
                                     approval_request_id="apr-001")
    r2 = await InboundService.create(_GRN, _IQC, _SKU, _QTY, _PRICE, _CUR, _SUP,
                                     approval_request_id="apr-001")
    assert r1["in_no"] == r2["in_no"]        # 幂等：同一审批单 → 同一入库单
    assert r1["approval_request_id"] == "apr-001"
    assert r1["status"] == "approved"        # IN 落库恒 approved（data_contract §5 口径 #1）
    assert r1["reviewed_by"] == "approval-flow"


@pytest.mark.asyncio
async def test_distinct_approval_ids_generate_distinct_inbounds():
    """不同审批单 → 不同入库单。"""
    r1 = await InboundService.create(_GRN, _IQC, _SKU, _QTY, _PRICE, _CUR, _SUP,
                                     approval_request_id="apr-001")
    r2 = await InboundService.create(_GRN, _IQC, _SKU, _QTY, _PRICE, _CUR, _SUP,
                                     approval_request_id="apr-002")
    assert r1["in_no"] != r2["in_no"]


@pytest.mark.asyncio
async def test_without_approval_id_always_new():
    """无 approval_request_id（工具必被审批拦截，正常不应发生）→ 每次新建（§6.10）。"""
    r1 = await InboundService.create(_GRN, _IQC, _SKU, _QTY, _PRICE, _CUR, _SUP)
    r2 = await InboundService.create(_GRN, _IQC, _SKU, _QTY, _PRICE, _CUR, _SUP)
    assert r1["in_no"] != r2["in_no"]
    assert r1["approval_request_id"] is None


# ---- P1-2 引用完整性校验（整改回归） ----


@pytest.mark.asyncio
async def test_rejects_unknown_grn():
    """GRN 不存在 → 拒绝入库（P1-2 整改，原实现不校验可悬挂过账）。"""
    with pytest.raises(NotFoundError, match="收货单不存在"):
        await InboundService.create("GRN-NOT-EXIST", _IQC, _SKU, _QTY, _PRICE, _CUR, _SUP,
                                    approval_request_id="apr-x1")


@pytest.mark.asyncio
async def test_rejects_failed_iqc():
    """IQC 判 fail → 拒绝过账（P1-2 整改，原实现 IQC fail 批次可过账）。"""
    with pytest.raises(RiskRejectedError, match="未通过"):
        await InboundService.create("GRN-20260820-012", "IQC-20260820-012",
                                    _SKU, 54000, _PRICE, _CUR, _SUP,
                                    approval_request_id="apr-x2")


@pytest.mark.asyncio
async def test_rejects_supplier_mismatch():
    """供应商与 PO 不一致 → 拒绝（P1-6 整改锚点：PO-20260818001 现归属 SUP-02）。"""
    with pytest.raises(BusinessError, match="供应商"):
        await InboundService.create(_GRN, _IQC, _SKU, _QTY, _PRICE, _CUR, "SUP-01",
                                    approval_request_id="apr-x3")


@pytest.mark.asyncio
async def test_rejects_price_mismatch():
    """单价与 PO 不一致 → 拒绝（P1-2 整改）。"""
    with pytest.raises(BusinessError, match="单价"):
        await InboundService.create(_GRN, _IQC, _SKU, _QTY, 9.9, _CUR, _SUP,
                                    approval_request_id="apr-x4")


@pytest.mark.asyncio
async def test_rejects_quantity_mismatch_grn_received():
    """入库数量与 GRN 实收不一致 → 拒绝（P1-2 整改，实收口径 §2.4）。"""
    with pytest.raises(BusinessError, match="实收数量"):
        await InboundService.create(_GRN, _IQC, _SKU, 12345, _PRICE, _CUR, _SUP,
                                    approval_request_id="apr-x5")


# ---- P0-4 库存联动（整改回归） ----


@pytest.mark.asyncio
async def test_inbound_restocks_inventory():
    """过账成功 → 库存台账 stock_qty 增加（P0-4 整改，原实现过账不更新库存）。"""
    before = (await InventoryService.get(_SKU))["stock_qty"]
    await InboundService.create(_GRN, _IQC, _SKU, _QTY, _PRICE, _CUR, _SUP,
                                approval_request_id="apr-restock")
    after = (await InventoryService.get(_SKU))["stock_qty"]
    assert after == before + _QTY


# ---- P0-4 落库持久化（跨进程语义回归） ----


@pytest.mark.asyncio
async def test_inbound_persisted_to_db(isolated_sqlite):
    """入库单落库：新连接可查（P0-4 整改，原内存态重启即失）。"""
    r = await InboundService.create(_GRN, _IQC, _SKU, _QTY, _PRICE, _CUR, _SUP,
                                    approval_request_id="apr-db")
    from app.db.connection import connection as db_conn
    with db_conn() as c:
        rows = c.query("SELECT * FROM inbound_records WHERE in_no=?", (r["in_no"],))
    assert len(rows) == 1
    assert rows[0]["approval_request_id"] == "apr-db"
