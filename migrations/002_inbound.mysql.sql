-- 002_inbound.mysql.sql：入库台账表（P0-4 整改：入库单/幂等从进程内存态落库）
-- 幂等：CREATE TABLE IF NOT EXISTS + 表内 KEY（MySQL 不支持 INDEX IF NOT EXISTS）
-- 占位符 %s（由 app/db/connection.py 适配）；approval_request_id UNIQUE：选项 B 幂等索引

CREATE TABLE IF NOT EXISTS inbound_records (
    in_no               VARCHAR(64) PRIMARY KEY,
    grn_no              VARCHAR(64) NOT NULL,
    iqc_no              VARCHAR(64) NOT NULL,
    sku                 VARCHAR(64) NOT NULL,
    inbound_qty         INT NOT NULL,
    unit_price          DOUBLE NOT NULL,
    currency            VARCHAR(16) NOT NULL,
    supplier_id         VARCHAR(64) NOT NULL,
    approval_request_id VARCHAR(64) UNIQUE,
    status              VARCHAR(32) NOT NULL DEFAULT 'approved',
    reviewed_by         VARCHAR(64) NOT NULL DEFAULT 'approval-flow',
    created_at          VARCHAR(64) NOT NULL,
    KEY idx_inbound_grn (grn_no),
    KEY idx_inbound_sku (sku)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
