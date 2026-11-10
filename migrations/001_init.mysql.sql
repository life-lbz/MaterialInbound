-- 001_init.mysql.sql：审计与审批表（MySQL 8 方言，utf8mb4 + InnoDB）
-- 幂等：CREATE TABLE IF NOT EXISTS + KEY（MySQL 不支持 INDEX IF NOT EXISTS，用表内 KEY 声明实现幂等）
-- 自增主键：BIGINT AUTO_INCREMENT；占位符 %s（由 app/db/connection.py 适配）；时间字段由应用侧写入 isoformat

CREATE TABLE IF NOT EXISTS audit_events (
    id             BIGINT NOT NULL AUTO_INCREMENT,
    correlation_id VARCHAR(255) NOT NULL,
    client_id      VARCHAR(255) NOT NULL,
    tool_name      VARCHAR(255) NOT NULL,
    args_summary   TEXT NOT NULL,
    status         VARCHAR(64) NOT NULL,
    latency_ms     DOUBLE,
    created_at     VARCHAR(64) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_audit_client (client_id),
    KEY idx_audit_tool (tool_name),
    KEY idx_audit_time (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS approval_requests (
    id             VARCHAR(64) PRIMARY KEY,
    tool_name      VARCHAR(255) NOT NULL,
    payload        TEXT NOT NULL,
    proposer       VARCHAR(255) NOT NULL,
    status         VARCHAR(32) NOT NULL DEFAULT 'pending',
    reviewer       VARCHAR(255),
    reason         VARCHAR(1024),
    amount_cny     DOUBLE,
    double_review  TINYINT NOT NULL DEFAULT 0,
    correlation_id VARCHAR(255),
    created_at     VARCHAR(64) NOT NULL,
    decided_at     VARCHAR(64),
    KEY idx_approval_status (status),
    KEY idx_approval_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
