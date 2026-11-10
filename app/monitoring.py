"""可观测性：固定窗口直方图指标 + 健康检查。

- 指标为计数器 + 延迟直方图（固定分桶，采样窗口有上限 METRICS_WINDOW，防内存泄漏，D13）。
- /metrics 输出 Prometheus 文本格式；/healthz 返回数据库连通性。
- 指标只暴露计数与耗时，不暴露业务数据（开发文档 §4.3）。
"""
from __future__ import annotations

from collections import defaultdict
from threading import Lock

from app.config import LATENCY_BUCKETS_MS, METRICS_WINDOW
from app.db.connection import ping_db

_lock = Lock()
_calls: dict[str, int] = defaultdict(int)
_errors: dict[str, int] = defaultdict(int)
_latency_samples: list[float] = []          # 环形窗口采样（ms）
_bucket_edges: list[float] = LATENCY_BUCKETS_MS


def record_call(tool_name: str) -> None:
    """记录一次工具调用（按工具名计数）。"""
    with _lock:
        _calls[tool_name] += 1


def record_error(tool_name: str) -> None:
    """记录一次工具调用失败（按工具名计数）。"""
    with _lock:
        _errors[tool_name] += 1


def record_latency(ms: float) -> None:
    """记录一次工具耗时（ms），窗口超出 METRICS_WINDOW 时截断旧样本。"""
    with _lock:
        _latency_samples.append(ms)
        if len(_latency_samples) > METRICS_WINDOW:
            del _latency_samples[: len(_latency_samples) - METRICS_WINDOW]


def _percentile_unlocked(ratio: float) -> float:
    """按样本计算分位延迟（ms），无样本返回 0。调用方必须已持有 _lock。"""
    if not _latency_samples:
        return 0.0
    values = sorted(_latency_samples)
    idx = min(len(values) - 1, int(len(values) * ratio))
    return round(values[idx], 2)


def _percentile(ratio: float) -> float:
    """按样本计算分位延迟（ms），无样本返回 0。"""
    with _lock:
        return _percentile_unlocked(ratio)


def _histogram_unlocked() -> list[tuple[float, int]]:
    """按固定分桶统计延迟分布（+Inf 桶并入最后一个分桶计数）。调用方必须已持有 _lock。"""
    buckets = [0] * (len(_bucket_edges) + 1)
    for v in _latency_samples:
        placed = False
        for i, edge in enumerate(_bucket_edges):
            if v <= edge:
                buckets[i] += 1
                placed = True
                break
        if not placed:
            buckets[-1] += 1
    return list(zip(_bucket_edges, buckets))


def _histogram() -> list[tuple[float, int]]:
    """按固定分桶统计延迟分布（+Inf 桶并入最后一个分桶计数）。"""
    with _lock:
        return _histogram_unlocked()


def metrics_text() -> str:
    """Prometheus 文本格式指标（text/plain; version=0.0.4）。

    注意：单次持锁，内部调用 *_unlocked 无锁核心，避免 threading.Lock 重入死锁。
    """
    with _lock:
        lines = ["# HELP miirp_tool_calls_total Tool calls total", "# TYPE miirp_tool_calls_total counter"]
        for name, n in sorted(_calls.items()):
            lines.append(f'miirp_tool_calls_total{{tool="{name}"}} {n}')
        lines.append("# HELP miirp_tool_errors_total Tool errors total")
        lines.append("# TYPE miirp_tool_errors_total counter")
        for name, n in sorted(_errors.items()):
            lines.append(f'miirp_tool_errors_total{{tool="{name}"}} {n}')
        lines.append("# HELP miirp_latency_ms Latency percentile")
        lines.append(f"miirp_latency_p50_ms {_percentile_unlocked(0.50)}")
        lines.append(f"miirp_latency_p95_ms {_percentile_unlocked(0.95)}")
        lines.append("# HELP miirp_latency_histogram Latency histogram buckets")
        hist = _histogram_unlocked()
        for edge, count in hist:
            lines.append(f"miirp_latency_histogram_bucket{{le=\"{edge:g}\"}} {count}")
        lines.append(f"miirp_latency_histogram_bucket{{le=\"+Inf\"}} {hist[-1][1]}")
        lines.append(f"miirp_latency_samples {len(_latency_samples)}")
        return "\n".join(lines) + "\n"


def healthz() -> dict:
    """健康检查：返回数据库连通性与服务版本（供 Docker HEALTHCHECK 与 Nginx 探活）。"""
    db_ok = ping_db()
    from app.config import SERVER_CFG
    return {"status": "ok" if db_ok else "degraded", "db": db_ok, "version": SERVER_CFG.get("version", "unknown")}
