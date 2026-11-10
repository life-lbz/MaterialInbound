"""审批状态机（落库版）：pending → approved | rejected | expired。

开发文档 §6.9（v2.0 修复 D5/D6）：
- D5：decide() 由审批工具/HTTP 端点调用，审批人跨进程可操作；
- D6：双人审批任一审批人拒绝，单据整体置 rejected（不再停留 pending）；
- 终态（approved/rejected/expired）不可再改：decide() 幂等返回 {"idempotent": True}；
- 状态以数据库为准；wait_for_decision 短轮询（0.5s，生产可换回调/Webhook）。
状态机（data_contract §2）：pending → approve(单) → approved；
pending → approve(双人一签) → pending；pending → approve(双人二签) → approved；
pending → reject(任一) → rejected；pending → 超时 → expired。

安全红线（RV-M2 P0-2/P0-3，DC-002）：
- P0-2：decide 落库前校验审批人 client_id 所属角色 ∈ APPROVE_ROLES（角色解析，禁止硬编码），
  未授权抛 PermissionDeniedError（code=PERMISSION_DENIED）；
- P0-3：双人审批第二签必须与第一签为不同审批人，同人重复决策拒绝。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.auth.registry import PermissionRegistry
from app.config import APPROVAL_TIMEOUT, APPROVE_ROLES
from app.db.connection import connection
from app.exceptions import PermissionDeniedError

# P0-2：审批人白名单由 APPROVE_ROLES 角色经 PermissionRegistry 解析（单一事实源，
# 不在此硬编码 client_id）；角色无 client_id 映射时跳过，避免 None 混入比较集合。
_APPROVER_CLIENT_IDS: frozenset[str] = frozenset(
    cid for r in APPROVE_ROLES
    if (cid := PermissionRegistry.client_id_for_role(r)) is not None
)

_POLL_INTERVAL = 0.5


class ApprovalService:
    """审批单生命周期：创建（仅 ApprovalMiddleware 调用，D1）/ 决策 / 轮询 / 待办查询。"""

    @staticmethod
    async def create(tool_name: str, payload: dict, proposer: str,
                     amount_cny: float | None = None,
                     double_review: bool = False,
                     correlation_id: str | None = None) -> dict:
        """创建审批单（唯一入口：ApprovalMiddleware，工具层不得自行创建，D1）。

        幂等键 = approval_requests.id（uuid4().hex[:12]），既作终态决策幂等键，
        又经选项 B 注入为入库单幂等键（data_contract §2）。
        """
        request_id = uuid.uuid4().hex[:12]
        with connection() as c:
            c.execute(
                "INSERT INTO approval_requests (id, tool_name, payload, proposer,"
                " status, amount_cny, double_review, correlation_id, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (request_id, tool_name, json.dumps(payload, ensure_ascii=False),
                 proposer, "pending", amount_cny, int(double_review),
                 correlation_id, datetime.now(timezone.utc).isoformat()),
            )
        return {"id": request_id, "status": "pending", "tool_name": tool_name,
                "payload": payload, "proposer": proposer, "amount_cny": amount_cny,
                "double_review": double_review, "correlation_id": correlation_id}

    @staticmethod
    async def wait_for_decision(request_id: str, timeout: float = APPROVAL_TIMEOUT) -> str:
        """短轮询等待审批终态（默认 APPROVAL_TIMEOUT=120s，0.5s 间隔）；超时置 expired。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = ApprovalService.get_status(request_id)
            if status in ("approved", "rejected"):
                return status
            await asyncio.sleep(_POLL_INTERVAL)
        with connection() as c:
            c.execute("UPDATE approval_requests SET status='expired' WHERE id=? AND status='pending'",
                      (request_id,))
        return "expired"

    @staticmethod
    def get_status(request_id: str) -> str:
        """查询审批单状态；单不存在返回 "unknown"。"""
        with connection() as c:
            rows = c.query("SELECT status FROM approval_requests WHERE id=?", (request_id,))
        return rows[0]["status"] if rows else "unknown"

    @staticmethod
    async def decide(request_id: str, approved: bool,
                     reviewer: str, reason: str = "") -> dict:
        """审批决策入口（由审批工具/HTTP 端点调用）。

        规则（§6.9）：
        - 单审批 → 直接置终态；
        - 双人审批 → 第一人通过后仍 pending 等第二签，任一审批人拒绝则整体 rejected（D6）；
        - 重复决策幂等：终态不可再改，返回 {"id", "status", "idempotent": True}。

        授权校验（P0-2/P0-3）：
        - 审批人 client_id 所属角色必须 ∈ APPROVE_ROLES，否则抛 PermissionDeniedError；
        - 双人审批第二签必须与第一签为不同审批人，同人重复决策抛 PermissionDeniedError。
        """
        # P0-2：审批人授权校验——reviewer 必须属于 APPROVE_ROLES 角色解析出的 client_id
        # 集合（角色解析，禁止硬编码）；"unknown" 兜底身份必然不在集合内，一并拒绝（D26）。
        if reviewer not in _APPROVER_CLIENT_IDS:
            raise PermissionDeniedError(
                f"审批人 {reviewer} 无审批权限（允许角色: {', '.join(sorted(APPROVE_ROLES))}）",
                detail=f"allowed client_id: {sorted(_APPROVER_CLIENT_IDS)}",
            )
        with connection() as c:
            rows = c.query("SELECT * FROM approval_requests WHERE id=?", (request_id,))
            if not rows:
                raise KeyError(f"审批单不存在: {request_id}")
            row = rows[0]
            if row["status"] in ("approved", "rejected", "expired"):
                return {"id": request_id, "status": row["status"], "idempotent": True}

            double = bool(row["double_review"])
            reviewers = [r for r in (row["reviewer"] or "").split(",") if r]

            # P0-3（整改）：同一审批单并发决策时，全部 UPDATE 必须带 status='pending'
            # 条件并检查 rowcount——并发下首个决策置终态后，后续决策命中 0 行即按
            # 终态幂等返回（对齐 wait_for_decision 的 L38 保护，消除双人并发互相覆盖）。
            def _refresh_status() -> dict:
                cur = c.query("SELECT status FROM approval_requests WHERE id=?", (request_id,))
                return {"id": request_id, "status": cur[0]["status"] if cur else "unknown",
                        "idempotent": True}

            # P0-3：双人审批第二签必须与第一签为不同审批人——同人重复决策拒绝
            # （覆盖通过/拒绝两条路径：第一签通过后，同一审批人不得再签第二签）。
            if double and reviewers and reviewer in reviewers:
                raise PermissionDeniedError(
                    f"双人审批第二签须为不同审批人，{reviewer} 已签第一签",
                    detail=f"first reviewer: {reviewers[0]}",
                )

            if not approved:
                # 任一审批人拒绝 → 整体拒绝（修复 D6；带 pending 条件防并发覆盖）
                affected = c.execute(
                    "UPDATE approval_requests SET status='rejected', reviewer=?, reason=?,"
                    " decided_at=? WHERE id=? AND status='pending'",
                    (",".join(reviewers + [reviewer]), reason,
                     datetime.now(timezone.utc).isoformat(), request_id))
                if affected == 0:
                    return _refresh_status()
                return {"id": request_id, "status": "rejected"}

            if double and not reviewers:
                # 双人第一签：通过 → 仍 pending
                affected = c.execute("UPDATE approval_requests SET reviewer=? WHERE id=? AND status='pending'",
                                     (reviewer, request_id))
                if affected == 0:
                    return _refresh_status()
                return {"id": request_id, "status": "pending"}

            if double and reviewers:
                # 双人第二签：通过 → approved
                affected = c.execute(
                    "UPDATE approval_requests SET status='approved', reviewer=?, reason=?,"
                    " decided_at=? WHERE id=? AND status='pending'",
                    (",".join(reviewers + [reviewer]), reason,
                     datetime.now(timezone.utc).isoformat(), request_id))
                if affected == 0:
                    return _refresh_status()
                return {"id": request_id, "status": "approved"}

            # 单审批
            affected = c.execute(
                "UPDATE approval_requests SET status='approved', reviewer=?, reason=?,"
                " decided_at=? WHERE id=? AND status='pending'",
                (reviewer, reason, datetime.now(timezone.utc).isoformat(), request_id))
            if affected == 0:
                return _refresh_status()
            return {"id": request_id, "status": "approved"}

    @staticmethod
    async def list_by_status(status: str = "pending") -> list[dict[str, Any]]:
        """按状态列出审批单（默认待办 pending），created_at DESC 排序。

        说明：定义为 async 以匹配两个调用方的 await 语义——
        api_contract §2.8 list_pending_approvals 工具与 server.py /approvals 路由
        （开发文档 §6.8/§6.6 均写 await；§6.9 字面为同步 def 属文档内部不一致，以调用方为准）。
        """
        with connection() as c:
            rows = c.query(
                "SELECT id, tool_name, proposer, status, amount_cny, double_review,"
                " correlation_id, created_at FROM approval_requests WHERE status=? ORDER BY created_at DESC",
                (status,))
        return rows
