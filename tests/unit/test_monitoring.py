"""可观测性测试（开发文档 §6.3，D13 回归，M1-02 验收）。

语义事实源（§6.3 L644-675、defect_regression D13）：
- 指标只暴露计数与耗时，不暴露业务数据（§4.3）。
- 直方图固定分桶、非累计（样本只计入第一个满足 le 的分桶）；+Inf 行计数 =
  最后一个有限桶计数（文档原样实现，zip 截断 +Inf 桶，观察项 OB-001 上报 A0）。
- metrics_text 单次持锁调用 *_unlocked（A2 已修复 §6.3 原样代码的 Lock 重入死锁）。
- /healthz 依赖 ping_db 探活（DB 连通 + 版本）。
"""
from __future__ import annotations

import threading

import pytest

import app.monitoring as mon
from app.monitoring import (
    healthz,
    metrics_text,
    record_call,
    record_error,
    record_latency,
)


@pytest.fixture(autouse=True)
def _reset_metrics():
    """每用例前后清空模块级指标态，避免用例间污染。"""
    with mon._lock:
        mon._calls.clear()
        mon._errors.clear()
        mon._latency_samples.clear()
    yield
    with mon._lock:
        mon._calls.clear()
        mon._errors.clear()
        mon._latency_samples.clear()


def test_record_call_and_error_counters():
    """工具调用/错误计数器（Prometheus 文本格式）。"""
    record_call("get_po")
    record_call("get_po")
    record_call("create_inbound_record")
    record_error("get_po")
    text = metrics_text()
    assert 'miirp_tool_calls_total{tool="get_po"} 2' in text
    assert 'miirp_tool_calls_total{tool="create_inbound_record"} 1' in text
    assert 'miirp_tool_errors_total{tool="get_po"} 1' in text


def test_histogram_bucket_counts():
    """直方图非累计分桶：样本只计入第一个满足 le 的分桶（§6.3 文档原样）。"""
    record_latency(10.0)
    record_latency(100.0)
    record_latency(100.0)
    text = metrics_text()
    assert 'miirp_latency_histogram_bucket{le="10"} 1' in text
    assert 'miirp_latency_histogram_bucket{le="100"} 2' in text
    assert 'miirp_latency_histogram_bucket{le="250"} 0' in text
    assert 'miirp_latency_samples 3' in text


def test_histogram_overflow_semantics():
    """超出最大分桶(2500ms)样本：有限桶不计入，+Inf 行=最后一个有限桶计数（文档原样，见 OB-001）。"""
    record_latency(5000.0)
    text = metrics_text()
    assert 'miirp_latency_histogram_bucket{le="2500"} 0' in text
    assert 'miirp_latency_histogram_bucket{le="+Inf"} 0' in text
    assert 'miirp_latency_samples 1' in text


def test_percentiles_p50_p95():
    """P50/P95 耗时（ms）：p50 取 int(n*0.5) 位，p95 取 int(n*0.95) 位（§6.3 文档原样）。"""
    record_latency(5.0)
    record_latency(10.0)
    record_latency(15.0)
    record_latency(20.0)
    text = metrics_text()
    assert 'miirp_latency_p50_ms 15.0' in text
    assert 'miirp_latency_p95_ms 20.0' in text


def test_empty_metrics_text():
    """无样本时 P50/P95=0、samples=0，直方图桶行仍输出。"""
    text = metrics_text()
    assert 'miirp_latency_p50_ms 0.0' in text
    assert 'miirp_latency_p95_ms 0.0' in text
    assert 'miirp_latency_samples 0' in text
    assert 'miirp_latency_histogram_bucket{le="+Inf"} 0' in text


def test_window_truncation(monkeypatch):
    """D13：环形窗口限长，超出 METRICS_WINDOW 截断旧样本（防内存泄漏）。"""
    monkeypatch.setattr(mon, "METRICS_WINDOW", 3)
    for i in range(5):
        record_latency(float(i))
    assert 'miirp_latency_samples 3' in metrics_text()


def test_metrics_text_no_deadlock():
    """A2 死锁修复：metrics_text 单次持锁调 *_unlocked，5s 内必须返回。"""
    record_call("get_po")
    record_latency(1.0)
    result: dict[str, str] = {}

    def worker() -> None:
        result["out"] = metrics_text()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "metrics_text 疑似死锁（5s 未返回）"
    assert "miirp_latency_histogram_bucket" in result["out"]


def test_metrics_text_concurrent_no_deadlock():
    """多线程并发调用 metrics_text 不重入死锁、不抛异常。"""
    results: list[str] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            results.append(metrics_text())
        except Exception as exc:  # noqa: BLE001 - 探活吞异常收集
            errors.append(exc)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert len(results) == 8


def test_healthz_db_ok(monkeypatch):
    """healthz：DB 连通 → status=ok，含版本（D31）。"""
    monkeypatch.setattr(mon, "ping_db", lambda: True)
    assert healthz() == {"status": "ok", "db": True, "version": "3.1.0"}


def test_healthz_db_down(monkeypatch):
    """healthz：DB 不可连 → status=degraded。"""
    monkeypatch.setattr(mon, "ping_db", lambda: False)
    h = healthz()
    assert h["status"] == "degraded"
    assert h["db"] is False
    assert h["version"] == "3.1.0"
