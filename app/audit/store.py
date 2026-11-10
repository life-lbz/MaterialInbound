"""审计事件落库（SQLite/PostgreSQL/MySQL 同构 SQL，占位符由连接层适配）。

开发文档 §6.9（v2.0 修复 D14/D15）：
- 表带自增主键 id（迁移 001 已更新），支持按 id DESC 分页查询（D15）；
- args_summary 记录"键名:类型:sha256(值前 8 位)"：可追溯具体参数且不落明文敏感值（D14）；
- TTL 保留策略：delete_before(created_before_iso) 供 scripts/archive_audit.py
  按 audit.retention_days（default 180 / production 365）清理（§4.7）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.db.connection import connection


@dataclass
class AuditEvent:
    """审计事件值对象：工具调用一次一条（含失败事件，status="error:<异常类名>"）。"""

    correlation_id: str
    client_id: str
    tool_name: str
    args_summary: str
    status: str
    latency_ms: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def summarize(value: object, limit: int = 300) -> str:
    """入参摘要化：键名 + 类型 + 值哈希（防敏感值落库，同时可追溯，D14）。

    - dict：{"键:类型:sha256前8位", ...}；
    - 其他："{类型}:{sha256前8位}"。
    摘要长度截断到 limit 字符。
    """
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            digest = hashlib.sha256(str(v).encode("utf-8")).hexdigest()[:8]
            parts.append(f"{k}:{type(v).__name__}:{digest}")
        text = "{" + ", ".join(parts) + "}"
        return text[:limit]
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
    return f"{type(value).__name__}:{digest}"[:limit]


class AuditStore:
    """审计事件存取：落库 / 分页查询 / TTL 清理（三方言同构 SQL）。"""

    @staticmethod
    def record(event: AuditEvent) -> None:
        """落库一条审计事件（时间由应用侧写入 isoformat，§4.6）。"""
        with connection() as c:
            c.execute(
                "INSERT INTO audit_events (correlation_id, client_id, tool_name,"
                " args_summary, status, latency_ms, created_at) VALUES (?,?,?,?,?,?,?)",
                (event.correlation_id, event.client_id, event.tool_name,
                 event.args_summary, event.status, event.latency_ms,
                 event.created_at.isoformat()),
            )

    @staticmethod
    def query(client_id: str | None = None,
              tool_name: str | None = None,
              limit: int = 100,
              offset: int = 0) -> list[dict[str, Any]]:
        """按条件分页查询，id DESC 排序（D15 分页：limit + offset，默认 100 条/页）。"""
        sql, params = "SELECT * FROM audit_events WHERE 1=1", []
        if client_id:
            sql, params = sql + " AND client_id=?", params + [client_id]
        if tool_name:
            sql, params = sql + " AND tool_name=?", params + [tool_name]
        with connection() as c:
            rows = c.query(sql + " ORDER BY id DESC LIMIT ? OFFSET ?",
                           params + [limit, offset])
        return rows

    @staticmethod
    def delete_before(created_before_iso: str) -> int:
        """按保留策略清理（§4.7）：删除 created_at < cutoff 的事件，返回删除行数。

        D15 回归锚点 tests/unit/test_audit_store.py::test_delete_before_ttl。
        """
        with connection() as c:
            before = c.query("SELECT COUNT(*) AS n FROM audit_events WHERE created_at < ?",
                             (created_before_iso,))[0]["n"]
            c.execute("DELETE FROM audit_events WHERE created_at < ?", (created_before_iso,))
        return before
