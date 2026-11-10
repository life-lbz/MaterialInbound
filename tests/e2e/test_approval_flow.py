"""MIIRP e2e：stdio 拉起真实 Server 的审批闭环全链路（M3-06，开发文档 §8.4）。

不依赖任何演示脚本，直接以 stdio 客户端（client/mcp_client.MCPClient）按角色
调用工具层（回归 D1/D5/D6/D16/D20/D26/D28/D32，依据 api_contract §5/§5.1/§6）：

- 通过路径：单人（低价值）与双人（高价值 amount_cny>=100000）审批 → 过账，
  inbound["approval_request_id"]==request_id（选项 B，D1 升级）；
- 拒绝路径：任一审批人拒绝 → submit 抛 RuntimeError（D28，isError 文本"审批未通过"），
  无入库单，审计落 error:<异常> 事件（data_contract §1.2 含失败事件）；
- 越权路径：grn-operator 调 approve_inbound_request → RuntimeError（D32，
  tools/call 白名单强制校验），决策不被接受、审批单状态不变；
- 角色最小可见：qty-check-agent 不可见审批工具（api_contract §4 裁剪）；
- 审计留痕：correlation_id 贯穿 approval_requests ↔ audit_events
  （data_contract §5 口径 #5 三向闭环）。

隔离：MIIRP_DB_DSN 指向 tmp_path 临时 SQLite（stdio 子进程继承 env，
app/config.py DB_DSN 环境变量优先），测试进程以 sqlite3 直查同一文件，
不污染 data/miirp.db，用例间互不干扰。
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from client.mcp_client import MCPClient

# 低价值单（amount = 0.0071 × 50000 × 7.1 = 2520.5 CNY < 100000，单审批；
# P1-8 整改：USD 金额经 fx_service 折算本位币，原 355.0 为未折算失真值）
_SUBMIT_LOW = {
    "grn_no": "GRN-20260820-011", "iqc_no": "IQC-20260820-011", "sku": "M-1001",
    "quantity": 50000, "unit_price": 0.0071, "currency": "USD", "supplier_id": "SUP-02",
}
# 高价值单（amount = 10.0 × 20000 = 200000 CNY >= 100000，双人审批；
# P0-4 整改：引用种子 GRN-20260820-021/IQC-20260820-021/PO-20260819003）
_SUBMIT_HIGH = {
    "grn_no": "GRN-20260820-021", "iqc_no": "IQC-20260820-021", "sku": "M-1001",
    "quantity": 20000, "unit_price": 10.0, "currency": "CNY", "supplier_id": "SUP-02",
}

_POLL_SECONDS = 30.0
_SUBMIT_TIMEOUT = 30.0


@pytest.fixture
def e2e_db(tmp_path, monkeypatch):
    """隔离 e2e 数据库：stdio 子进程继承 MIIRP_DB_DSN（config DB_DSN 环境变量优先）。"""
    db_path = tmp_path / "miirp_e2e.db"
    monkeypatch.setenv("MIIRP_DB_DSN", f"sqlite:///{db_path.as_posix()}")
    return db_path


def _rows(db_path, sql, params=()):
    """直查临时库（e2e 进程不 import app.db.connection，避开模块级 DSN 常量）。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


async def _pending_event(wh, poll_seconds=_POLL_SECONDS) -> dict:
    """轮询审批待办，返回 tool_name=create_inbound_record 的最新一条（防历史残留）。"""
    deadline = time.monotonic() + poll_seconds
    while time.monotonic() < deadline:
        raw = await wh.call_tool("list_pending_approvals", {})
        items = raw.get("result") if isinstance(raw, dict) else raw
        items = [i for i in (items if isinstance(items, list) else [])
                 if i.get("tool_name") == "create_inbound_record"]
        if items:
            return max(items, key=lambda i: i.get("created_at", ""))
        await asyncio.sleep(0.5)
    raise TimeoutError("等待审批单超时（ApprovalMiddleware 未创建审批单）")


async def _submit(review, args):
    """提交入库单（ApprovalMiddleware 阻塞等审批），超时保护防 120s 挂死。"""
    return await asyncio.wait_for(review.call_tool("create_inbound_record", args),
                                  timeout=_SUBMIT_TIMEOUT)


@pytest.mark.asyncio
async def test_approval_flow_single_reviewer_end_to_end(e2e_db):
    """单人通过全链路：提交 → 审批 → 过账 → 审计留痕（correlation_id 贯穿）。"""
    async with MCPClient("review-agent") as review, MCPClient("warehouse-manager") as wh:
        submit = asyncio.create_task(_submit(review, _SUBMIT_LOW))
        event = await _pending_event(wh)
        assert event["proposer"] == "review-001"          # 提议人 = client_id（api_contract §4）
        assert event["double_review"] == 0                # 低价值单 → 单审批
        assert event["amount_cny"] == 2520.5              # P1-8 整改：USD 折算本位币（355×7.1）
        decision = await wh.call_tool("approve_inbound_request", {
            "request_id": event["id"], "approved": True, "reason": "e2e 通过"})
        assert decision["status"] == "approved"

        inbound = await submit
        assert "in_no" in inbound                         # 审批通过后过账生成入库单
        assert inbound["status"] == "approved"
        assert inbound["reviewed_by"] == "approval-flow"
        assert inbound["approval_request_id"] == event["id"]   # 选项 B（M3-06 验收锚点）

    # 审计留痕：create_inbound_record 落 ok 事件，correlation_id 与审批单一致（三向闭环）
    evts = _rows(e2e_db, "SELECT correlation_id, status FROM audit_events"
                 " WHERE tool_name=? AND client_id=? ORDER BY id DESC",
                 ("create_inbound_record", "review-001"))
    assert evts and evts[0]["status"] == "ok"
    apr = _rows(e2e_db, "SELECT correlation_id FROM approval_requests WHERE id=?",
                (event["id"],))[0]["correlation_id"]
    assert apr and apr == evts[0]["correlation_id"]


@pytest.mark.asyncio
async def test_high_value_double_review_end_to_end(e2e_db):
    """双人通过路径：高价值单两签（wh-001 + comp-001）通过后才放行过账。"""
    async with (MCPClient("review-agent") as review,
                MCPClient("warehouse-manager") as wh,
                MCPClient("compliance-officer") as comp):
        submit = asyncio.create_task(_submit(review, _SUBMIT_HIGH))
        event = await _pending_event(wh)
        assert event["double_review"] == 1                # 高价值置双人审批
        assert event["amount_cny"] == 200000.0
        # 第一签通过仍 pending（等第二签，§6.9）
        first = await wh.call_tool("approve_inbound_request", {
            "request_id": event["id"], "approved": True, "reason": "第一签"})
        assert first["status"] == "pending"
        # 第二签（不同审批人 comp-001）通过 → approved
        second = await comp.call_tool("approve_inbound_request", {
            "request_id": event["id"], "approved": True, "reason": "第二签"})
        assert second["status"] == "approved"

        inbound = await submit
        assert inbound["approval_request_id"] == event["id"]
        assert inbound["status"] == "approved"

    row = _rows(e2e_db, "SELECT reviewer, status FROM approval_requests WHERE id=?",
                (event["id"],))[0]
    assert row["status"] == "approved"
    assert row["reviewer"] == "wh-001,comp-001"           # 双人两签留痕（D26 身份服务端解析）


@pytest.mark.asyncio
async def test_approval_reject_blocks_posting(e2e_db):
    """拒绝路径：任一审批人拒绝 → submit 抛 RuntimeError（D28），无入库单，审计落失败事件。"""
    async with MCPClient("review-agent") as review, MCPClient("warehouse-manager") as wh:
        submit = asyncio.create_task(_submit(review, _SUBMIT_LOW))
        event = await _pending_event(wh)
        decision = await wh.call_tool("approve_inbound_request", {
            "request_id": event["id"], "approved": False, "reason": "价格存疑"})
        assert decision["status"] == "rejected"

        # D28：审批拒绝 → 工具返回 isError → mcp_client 抛 RuntimeError（不做 json.loads）
        with pytest.raises(RuntimeError, match="审批未通过"):
            await submit

    row = _rows(e2e_db, "SELECT status FROM approval_requests WHERE id=?",
                (event["id"],))[0]
    assert row["status"] == "rejected"                    # D6：任一拒绝 → 整体 rejected
    # 审计含失败事件（data_contract §1.2：status="error:<异常类名>"）
    evts = _rows(e2e_db, "SELECT status FROM audit_events WHERE tool_name=?"
                 " AND client_id=? ORDER BY id DESC",
                 ("create_inbound_record", "review-001"))
    assert evts and any(r["status"].startswith("error:") for r in evts), (
        f"应有失败审计事件, got {[r['status'] for r in evts]}")


@pytest.mark.asyncio
async def test_role_minimal_visibility(e2e_db):
    """角色最小可见：qty-check-agent 不可见审批工具，warehouse-manager 可见。"""
    async with MCPClient("qty-check-agent") as qty, MCPClient("warehouse-manager") as wh:
        qty_names = {t.name for t in await qty.list_tools()}
        assert "approve_inbound_request" not in qty_names
        assert "list_pending_approvals" not in qty_names
        assert {"get_po", "get_grn"} <= qty_names

        wh_names = {t.name for t in await wh.list_tools()}
        assert {"approve_inbound_request", "list_pending_approvals",
                "create_inbound_record"} <= wh_names


@pytest.mark.asyncio
async def test_unauthorized_approve_denied(e2e_db):
    """越权回归（D32）：grn-operator 调 approve_inbound_request 被拒、状态不变；
    合法审批人补签仍通过（拒绝仅因授权，审批机制未破坏）。"""
    async with (MCPClient("review-agent") as review,
                MCPClient("warehouse-manager") as wh,
                MCPClient("grn-operator") as grn):
        submit = asyncio.create_task(_submit(review, _SUBMIT_LOW))
        event = await _pending_event(wh)
        # tools/call 白名单强制校验：grn-operator 无权调用审批工具（D32）
        with pytest.raises(RuntimeError, match="无权调用"):
            await grn.call_tool("approve_inbound_request", {
                "request_id": event["id"], "approved": True, "reason": "越权"})
        # §5.1：决策不被接受、状态不变
        row = _rows(e2e_db, "SELECT status FROM approval_requests WHERE id=?",
                    (event["id"],))[0]
        assert row["status"] == "pending"
        # 合法审批人补签 → 通过
        await wh.call_tool("approve_inbound_request", {
            "request_id": event["id"], "approved": True, "reason": "合法补签"})
        inbound = await submit
        assert inbound["approval_request_id"] == event["id"]
