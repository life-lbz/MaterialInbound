"""审批状态机全分支（开发文档 §8.2 语义、data_contract §2、D5/D6/D32 回归）。

用例与 §8.2 test_approval.py 对齐：
- 单审批通过/拒绝；
- 双人审批两签通过（第一签仍 pending，第二签 approved）；
- 双人第二签拒绝 → 整体 rejected（D6）；
- 双人第一签拒绝 → 直接整体 rejected（D6）；
- 终态幂等：终态不可再改（decide 返回 idempotent=True）；
- wait_for_decision 短超时 → expired（data_contract §2 状态机超时分支）。

安全回归（DC-002、RV-M2 P0-2/P0-3，api_contract §5.1；decide 层直测锚点，
集成层经 tools/call 时 P0-1 白名单先拦截，见 tests/integration/test_approval_flow.py）：
- 非审批角色 client_id decide → PermissionDeniedError（reviewer 所属角色 ∉ APPROVE_ROLES）；
- 双人审批同人两签 → 第二签 PermissionDeniedError（异人校验）。
"""
from __future__ import annotations

import pytest

from app.approval.service import ApprovalService
from app.exceptions import PermissionDeniedError


@pytest.fixture(autouse=True)
def _db(isolated_sqlite):
    """审批单落库需真实数据库（data_contract §1.3），全部用例走临时 SQLite。"""
    return isolated_sqlite


@pytest.mark.asyncio
async def test_approve_single_reviewer():
    req = await ApprovalService.create("create_inbound_record", {"g": "1"}, "review-001")
    res = await ApprovalService.decide(req["id"], approved=True, reviewer="wh-001")
    assert res["status"] == "approved"
    assert await ApprovalService.wait_for_decision(req["id"], timeout=5) == "approved"


@pytest.mark.asyncio
async def test_reject_single_reviewer():
    req = await ApprovalService.create("create_inbound_record", {"g": "2"}, "review-001")
    res = await ApprovalService.decide(req["id"], approved=False, reviewer="wh-001")
    assert res["status"] == "rejected"
    assert await ApprovalService.wait_for_decision(req["id"], timeout=5) == "rejected"


@pytest.mark.asyncio
async def test_double_review_both_approve():
    """双人审批两签通过：第一签仍 pending，第二签 approved（data_contract §2）。"""
    req = await ApprovalService.create(
        "create_inbound_record", {"g": "3"}, "review-001",
        amount_cny=200000.0, double_review=True)
    await ApprovalService.decide(req["id"], approved=True, reviewer="wh-001")
    assert ApprovalService.get_status(req["id"]) == "pending"   # 第一签仍挂起
    await ApprovalService.decide(req["id"], approved=True, reviewer="comp-001")
    assert await ApprovalService.wait_for_decision(req["id"], timeout=5) == "approved"


@pytest.mark.asyncio
async def test_double_review_second_reject_goes_rejected():
    """D6 回归：双人审批第二人拒绝 → 整体 rejected（而非停留 pending）。"""
    req = await ApprovalService.create(
        "create_inbound_record", {"g": "4"}, "review-001",
        amount_cny=200000.0, double_review=True)
    await ApprovalService.decide(req["id"], approved=True, reviewer="wh-001")
    await ApprovalService.decide(req["id"], approved=False, reviewer="comp-001", reason="价格存疑")
    assert ApprovalService.get_status(req["id"]) == "rejected"


@pytest.mark.asyncio
async def test_double_review_first_reject():
    """D6 回归：第一人拒绝 → 直接整体 rejected。"""
    req = await ApprovalService.create(
        "create_inbound_record", {"g": "5"}, "review-001",
        amount_cny=200000.0, double_review=True)
    await ApprovalService.decide(req["id"], approved=False, reviewer="wh-001")
    assert ApprovalService.get_status(req["id"]) == "rejected"


@pytest.mark.asyncio
async def test_decide_idempotent():
    """终态不可再改（幂等，data_contract §2）：approved 后再拒绝仍保持 approved。"""
    req = await ApprovalService.create("create_inbound_record", {"g": "6"}, "review-001")
    await ApprovalService.decide(req["id"], approved=True, reviewer="wh-001")
    res = await ApprovalService.decide(req["id"], approved=False, reviewer="wh-001")
    assert res["status"] == "approved"
    assert res["idempotent"] is True
    assert ApprovalService.get_status(req["id"]) == "approved"


@pytest.mark.asyncio
async def test_decide_unknown_request_raises():
    """审批单不存在 → decide 抛 KeyError（api_contract §2.8）。"""
    with pytest.raises(KeyError):
        await ApprovalService.decide("no-such-id", approved=True, reviewer="wh-001")


@pytest.mark.asyncio
async def test_wait_for_decision_timeout_expired():
    """等待超时 → 置 expired 并返回（data_contract §2 状态机超时分支）。"""
    req = await ApprovalService.create("create_inbound_record", {"g": "7"}, "review-001")
    assert await ApprovalService.wait_for_decision(req["id"], timeout=0.01) == "expired"
    assert ApprovalService.get_status(req["id"]) == "expired"


@pytest.mark.asyncio
async def test_list_by_status_pending():
    """待办查询：pending 列表含新建审批单（api_contract §2.8 字段契约）。"""
    await ApprovalService.create("create_inbound_record", {"g": "8"}, "review-001",
                                 amount_cny=5000.0, double_review=False)
    rows = await ApprovalService.list_by_status("pending")
    assert len(rows) >= 1
    row = rows[0]
    for key in ("id", "tool_name", "proposer", "status", "amount_cny",
                "double_review", "correlation_id", "created_at"):
        assert key in row
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_decide_non_approver_denied():
    """P0-2（api_contract §5.1）：decide 层授权校验——非审批角色 client_id（grn-001，
    所属角色 grn-operator ∉ APPROVE_ROLES）决策 → PermissionDeniedError，
    决策不被接受、审批单状态不变。"""
    req = await ApprovalService.create("create_inbound_record", {"g": "p02"}, "review-001")
    with pytest.raises(PermissionDeniedError) as exc_info:
        await ApprovalService.decide(req["id"], approved=True, reviewer="grn-001")
    assert exc_info.value.code == "PERMISSION_DENIED"
    assert ApprovalService.get_status(req["id"]) == "pending"   # §5.1"决策不被接受、状态不变"


@pytest.mark.asyncio
async def test_decide_same_reviewer_double_sign_denied():
    """P0-3（api_contract §5.1）：decide 层异人校验——双人审批同人两签：
    第一签通过后，同一审批人第二签 → PermissionDeniedError，未置 approved。"""
    req = await ApprovalService.create(
        "create_inbound_record", {"g": "p03"}, "review-001",
        amount_cny=200000.0, double_review=True)
    await ApprovalService.decide(req["id"], approved=True, reviewer="wh-001")
    assert ApprovalService.get_status(req["id"]) == "pending"   # 第一签仍挂起
    with pytest.raises(PermissionDeniedError) as exc_info:
        await ApprovalService.decide(req["id"], approved=True, reviewer="wh-001")
    assert exc_info.value.code == "PERMISSION_DENIED"
    assert ApprovalService.get_status(req["id"]) == "pending"   # 同人第二签被拒，未置 approved
