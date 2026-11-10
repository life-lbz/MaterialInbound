-- 001_init.sqlite.sql：审计与审批表（SQLite 方言）
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS；占位符为原生 ?（由 app/db/connection.py 适配）
-- 自增主键：INTEGER PRIMARY KEY AUTOINCREMENT；时间字段由应用侧写入 isoformat（不依赖数据库时间函数）

CREATE TABLE IF NOT EXISTS audit_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id TEXT NOT NULL,
    client_id      TEXT NOT NULL,
    tool_name      TEXT NOT NULL,
    args_summary   TEXT NOT NULL,
    status         TEXT NOT NULL,
    latency_ms     REAL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_client ON audit_events (client_id);
CREATE INDEX IF NOT EXISTS idx_audit_tool   ON audit_events (tool_name);
CREATE INDEX IF NOT EXISTS idx_audit_time   ON audit_events (created_at);

CREATE TABLE IF NOT EXISTS approval_requests (
    id             TEXT PRIMARY KEY,
    tool_name      TEXT NOT NULL,
    payload        TEXT NOT NULL,
    proposer       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    reviewer       TEXT,
    reason         TEXT,
    amount_cny     REAL,
    double_review  INTEGER NOT NULL DEFAULT 0,
    correlation_id TEXT,
    created_at     TEXT NOT NULL,
    decided_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_requests (status);
CREATE INDEX IF NOT EXISTS idx_approval_created ON approval_requests (created_at);
