"""JSON 结构化日志测试（开发文档 §6.2，D22 回归锚点）。

锚点（defect_regression D22）：tests/unit/test_logging_setup.py::test_json_trace_id
语义（§6.2）：stdout JSON 行含 ts/level/logger/message/trace_id；
trace_id 经 contextvars 贯穿（set_trace_id → 同 context 日志携带），日志与审计共享。
"""
from __future__ import annotations

import json
import logging

import pytest

from app.logging_setup import (
    get_trace_id,
    log_with,
    set_trace_id,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _restore_root_handlers():
    """保存并恢复 root handlers，避免污染其它测试与进程日志。"""
    root = logging.getLogger()
    saved = list(root.handlers)
    yield
    root.handlers[:] = saved


def test_json_trace_id(capsys):
    """D22 回归锚点：JSON 日志行包含 trace_id 字段。"""
    setup_logging(level="INFO", json_mode=True)
    set_trace_id("trace-abc-123")
    logging.getLogger("miirp").info("入库审批通过")
    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["trace_id"] == "trace-abc-123"
    assert payload["message"] == "入库审批通过"
    assert payload["level"] == "INFO"


def test_json_fields_present(capsys):
    """JSON 行含 ts/level/logger/message/trace_id 全字段。"""
    setup_logging()
    set_trace_id(None)
    logging.getLogger("miirp.config").warning("deprecated")
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert {"ts", "level", "logger", "message", "trace_id"} <= set(payload)
    assert payload["logger"] == "miirp.config"
    assert payload["level"] == "WARNING"


def test_json_trace_id_null_when_unset(capsys):
    """未设置 trace_id 时字段为 null（而非缺失键）。"""
    setup_logging()
    set_trace_id(None)
    logging.getLogger("miirp").info("no trace")
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["trace_id"] is None


def test_log_with_extra_fields(capsys):
    """log_with：extra 字段并入 JSON，且保留 trace_id。"""
    setup_logging()
    set_trace_id("t-1")
    log_with({"client_id": "c-001", "request_id": "r-1"})
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["client_id"] == "c-001"
    assert payload["request_id"] == "r-1"
    assert payload["trace_id"] == "t-1"


def test_text_mode_when_json_disabled(capsys):
    """json_mode=False 输出文本格式（非 JSON）。"""
    setup_logging(json_mode=False)
    logging.getLogger("miirp").info("plain")
    out = capsys.readouterr().out.strip()
    assert "plain" in out
    assert not out.startswith("{")


def test_set_get_trace_id_isolated():
    """contextvars 基本行为：set 后同 context 可读，可清空。"""
    set_trace_id("tid-A")
    assert get_trace_id() == "tid-A"
    set_trace_id(None)
    assert get_trace_id() is None


def test_json_formatter_exc_info(capsys):
    """异常日志携带 exc 字段（内部细节）。"""
    setup_logging()
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("miirp").exception("failed")
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "exc" in payload
    assert "ValueError" in payload["exc"]
