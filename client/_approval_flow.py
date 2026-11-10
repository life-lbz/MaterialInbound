"""审批流公共辅助（A6 内部，供两个演示脚本复用）。

演示"Agent 建议 → 审批 → 审计"全链路的提交与审批步骤：
1. review-agent 身份提交 create_inbound_record（ApprovalMiddleware 阻塞等待审批）；
2. warehouse-manager 身份轮询审批待办（list_pending_approvals），
   匹配 tool_name=create_inbound_record 且 created_at 最新的待办（防历史残留干扰）；
3. approve_inbound_request 审批通过；提交任务返回入库单（含 approval_request_id）。

审批人恒用 warehouse-manager 身份（协作方案 §3.7 最小权限，api_contract §2.8）。
correlation_id 由审批待办项携带（审批中间件与审计中间件同算法，
开发文档 §2.5/§6.7：approval_requests ↔ audit_events 三向闭环）。
"""
from __future__ import annotations

import asyncio
import time

_POLL_INTERVAL = 0.5


async def submit_and_approve(grn: dict, review_client, wh_client,
                             poll_seconds: float = 30.0) -> dict:
    """提交入库单并审批通过，返回 {inbound, approval_event, decision}。

    - inbound: create_inbound_record 返回（in_no/approval_request_id/status/reviewed_by）；
    - approval_event: 命中的审批待办项（id/correlation_id/amount_cny/double_review/...）；
    - decision: approve_inbound_request 返回（{id, status}，双人一签时为 pending）。
    """
    submit = asyncio.create_task(review_client.call_tool("create_inbound_record", {
        "grn_no": grn["grn_no"], "iqc_no": grn["iqc_no"], "sku": grn["sku"],
        "quantity": grn["quantity"], "unit_price": grn["unit_price"],
        "currency": grn["currency"], "supplier_id": grn["supplier_id"],
    }))

    request_id = None
    approval_event = None
    deadline = time.monotonic() + poll_seconds
    while time.monotonic() < deadline:
        raw = await wh_client.call_tool("list_pending_approvals", {})
        # fastmcp 对 list 返回值把 structuredContent 包为 {"result": [...]}（D16 解析），
        # 兼容 dict 包裹与直接 list 两种形态
        items_raw = raw.get("result") if isinstance(raw, dict) else raw
        items = [i for i in (items_raw if isinstance(items_raw, list) else [])
                 if i.get("tool_name") == "create_inbound_record"]
        if items:
            # 取 created_at 最新一条（ISO UTC 字符串可直接比较），防历史残留干扰
            approval_event = max(items, key=lambda i: i.get("created_at", ""))
            request_id = approval_event["id"]
            break
        await asyncio.sleep(_POLL_INTERVAL)
    if request_id is None:
        raise TimeoutError("等待审批单超时（ApprovalMiddleware 未创建审批单）")

    decision = await wh_client.call_tool("approve_inbound_request", {
        "request_id": request_id, "approved": True,
        "reason": "演示审批通过（四路证据齐备、风险可控）",
    })
    inbound = await submit      # 审批通过后中间件放行并执行工具
    return {"inbound": inbound, "approval_event": approval_event, "decision": decision}


__all__ = ["submit_and_approve"]
