"""审计存储落库/查询/TTL（开发文档 §6.9、data_contract §1.2、D15 回归）。

D15 回归锚点：tests/unit/test_audit_store.py::test_delete_before_ttl
（AuditStore.delete_before 按 created_at 清理，§4.7 TTL 保留策略）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.audit.store import AuditEvent, AuditStore


@pytest.fixture(autouse=True)
def _db(isolated_sqlite):
    """审计事件落库需真实数据库（data_contract §1.2），全部用例走临时 SQLite。"""
    return isolated_sqlite


def _event(client_id: str = "qa-001", tool: str = "get_po", status: str = "ok",
           created_at: datetime | None = None) -> AuditEvent:
    return AuditEvent(
        correlation_id="corr-1",
        client_id=client_id,
        tool_name=tool,
        args_summary="{po_no:str:abc12345}",
        status=status,
        latency_ms=12.3,
        created_at=created_at or datetime.now(timezone.utc),
    )


def test_record_and_query_by_client():
    """落库一条并按 client_id 查询（含字段契约对齐 data_contract §1.2）。"""
    AuditStore.record(_event(client_id="qa-001"))
    rows = AuditStore.query(client_id="qa-001")
    assert len(rows) == 1
    row = rows[0]
    assert row["correlation_id"] == "corr-1"
    assert row["client_id"] == "qa-001"
    assert row["tool_name"] == "get_po"
    assert row["args_summary"] == "{po_no:str:abc12345}"
    assert row["status"] == "ok"
    assert row["latency_ms"] == 12.3
    assert row["created_at"]  # 应用侧写入 isoformat


def test_query_filter_by_tool_and_limit():
    """tool_name 过滤 + limit 分页（id DESC，D15 分页语义）。"""
    for i in range(5):
        AuditStore.record(_event(client_id="qa-001", tool="get_po"))
    AuditStore.record(_event(client_id="qa-001", tool="get_grn"))
    rows = AuditStore.query(client_id="qa-001", tool_name="get_po", limit=3)
    assert len(rows) == 3
    grn_rows = AuditStore.query(client_id="qa-001", tool_name="get_grn")
    assert len(grn_rows) == 1


def test_query_orders_by_id_desc():
    """按 id DESC 排序（§6.9），最新记录在前。"""
    AuditStore.record(_event(client_id="qa-001", tool="get_po"))
    AuditStore.record(_event(client_id="qa-001", tool="get_grn"))
    rows = AuditStore.query(client_id="qa-001")
    assert rows[0]["tool_name"] == "get_grn"
    assert rows[1]["tool_name"] == "get_po"


def test_delete_before_ttl():
    """D15 回归锚点：delete_before 按 created_at 清理旧事件、保留新事件并返回删除行数。"""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=200)      # 超保留期（default retention_days=180）
    fresh = now - timedelta(days=10)
    AuditStore.record(_event(client_id="qa-001", tool="get_po", created_at=old))
    AuditStore.record(_event(client_id="qa-001", tool="get_grn", created_at=fresh))
    deleted = AuditStore.delete_before((now - timedelta(days=180)).isoformat())
    assert deleted == 1
    rows = AuditStore.query(client_id="qa-001")
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "get_grn"


def test_delete_before_nothing_to_delete():
    """全部事件都在保留期内 → 删除 0 行，数据不动。"""
    now = datetime.now(timezone.utc)
    AuditStore.record(_event(client_id="qa-001", tool="get_po", created_at=now))
    deleted = AuditStore.delete_before((now - timedelta(days=1)).isoformat())
    assert deleted == 0
    assert len(AuditStore.query(client_id="qa-001")) == 1
