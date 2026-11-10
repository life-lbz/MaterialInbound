"""JSON 结构化日志：统一时间/级别/模块/trace_id 字段，输出到 stdout。

trace_id 通过 contextvars 贯穿一次请求，入口中间件生成后，
日志与审计共享同一 trace_id，支持跨模块对账（D22）。
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def set_trace_id(tid: str | None) -> None:
    """设置当前 context 的 trace_id（入口中间件调用）。"""
    trace_id_var.set(tid)


def get_trace_id() -> str | None:
    """读取当前 context 的 trace_id（日志与审计共享）。"""
    return trace_id_var.get()


class JsonFormatter(logging.Formatter):
    """JSON 行日志格式化器：ts/level/logger/message/trace_id，可选 exc 与 extra 字段。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": get_trace_id() or record.__dict__.get("trace_id"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_mode: bool = True) -> None:
    """初始化根日志器：重置处理器，挂 JSON（或纯文本）stdout 处理器。"""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_mode else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


def log_with(extra: dict, *, level: str = "info") -> None:
    """带附加字段的日志（如 client_id、request_id）。"""
    logger = logging.getLogger("miirp")
    logger.log(getattr(logging, level.upper(), logging.INFO), "", extra={"extra_fields": extra})
