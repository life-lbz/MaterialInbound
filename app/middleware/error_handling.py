"""业务错误码透传中间件（P2-4 整改）。

背景：fastmcp 内置 ErrorHandlingMiddleware 将非 MCP 异常统一归一为
McpError(-32603, "Internal error: ...")，业务错误码（PERMISSION_DENIED /
RATE_LIMITED / APPROVAL_REJECTED / APPROVAL_TIMEOUT / RISK_REJECTED / NOT_FOUND 等）
在传输层丢失语义，客户端无法按错误码分支处理。

整改：子类化内置中间件，仅覆盖 _transform_error：
- BusinessError 子类 → 保留 message（对外脱敏由 mask_error_details 语义处理），
  业务错误码写入 ErrorData.data.code（JSON-RPC error.data 透传，客户端可读）；
- 其余异常路径完全继承内置行为（不改变协议语义与既有测试断言）。
"""
from __future__ import annotations

from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.middleware.error_handling import (
    ErrorHandlingMiddleware as _BaseErrorHandlingMiddleware,
)
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from app.exceptions import BusinessError

# 业务错误统一映射到 MCP 协议应用错误码区间（-32000 内）；具体业务码随 data.code 透传
_BUSINESS_MCP_CODE = -32000


class BusinessErrorHandlingMiddleware(_BaseErrorHandlingMiddleware):
    """在 fastmcp 错误归一基础上保留业务错误码（P2-4 整改，其余行为继承内置）。"""

    def _transform_error(self, error: Exception, context: MiddlewareContext) -> Exception:
        if isinstance(error, BusinessError):
            # BusinessError.message 已自带 "{code}: " 前缀（exceptions.py，P2-4 整改），
            # 此处保留原样透传；ErrorData.data 同时保留结构化业务码/内部细节，
            # 供原始 JSON-RPC 协议层消费（fastmcp 客户端仅透传文本时的兜底）。
            return McpError(ErrorData(
                code=_BUSINESS_MCP_CODE,
                message=error.message or str(error),
                data={"code": error.code, "detail": error.detail},
            ))
        return super()._transform_error(error, context)


__all__ = ["BusinessErrorHandlingMiddleware"]
