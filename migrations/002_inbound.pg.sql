-- 002_inbound.pg.sql：入库台账表（P0-4 整改：入库单/幂等从进程内存态落库）
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS；占位符 %s（由 app/db/connection.py 适配）
-- approval_request_id UNIQUE：选项 B 幂等索引（PG 中 UNIQUE 列允许多个 NULL）

CREATE TABLE IF NOT EXISTS inbound_records (
    in_no               TEXT PRIMARY KEY,
    grn_no              TEXT NOT NULL,
    iqc_no              TEXT NOT NULL,
    sku                 TEXT NOT NULL,
    inbound_qty         INTEGER NOT NULL,
    unit_price          DOUBLE PRECISION NOT NULL,
    currency            TEXT NOT NULL,
    supplier_id         TEXT NOT NULL,
    approval_request_id TEXT UNIQUE,
    status              TEXT NOT NULL DEFAULT 'approved',
    reviewed_by         TEXT NOT NULL DEFAULT 'approval-flow',
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbound_grn ON inbound_records (grn_no);
CREATE INDEX IF NOT EXISTS idx_inbound_sku ON inbound_records (sku);
