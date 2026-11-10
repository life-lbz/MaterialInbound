"""业务异常体系：统一转译为 MCP ToolError（开发文档 §6.4）。

所有业务异常继承 BusinessError，携带对外可见的稳定错误码；
内部细节（detail）不入对外响应（mask_error_details=true）。

P2-4 整改：对外 message 自带业务码前缀 "{code}: {message}"——fastmcp
工具执行层/传输层仅透传异常文本（不携带结构化错误码字段），客户端按
"{code}: " 前缀即可稳定区分错误类型（如 APPROVAL_REJECTED / RATE_LIMITED /
PERMISSION_DENIED），不再归一为 Generic Error。
"""
from __future__ import annotations


class BusinessError(Exception):
    """业务异常基类：携带对外可见的稳定错误码。"""

    code = "BUSINESS_ERROR"

    def __init__(self, message: str, *, detail: str | None = None):
        # P2-4：原始文案与带码文案分离——raw_message 供内部引用，
        # message/__str__ 对外自带 "{code}: " 前缀（跨框架层透传的唯一可靠通道）。
        self.raw_message = message
        self.message = f"{self.code}: {message}"
        super().__init__(self.message)
        self.detail = detail          # 内部细节，不入对外响应


class NotFoundError(BusinessError):
    """资源不存在。"""

    code = "NOT_FOUND"


class PermissionDeniedError(BusinessError):
    """权限不足。"""

    code = "PERMISSION_DENIED"


class ApprovalRejectedError(BusinessError):
    """审批被拒绝。"""

    code = "APPROVAL_REJECTED"


class ApprovalTimeoutError(BusinessError):
    """审批等待超时。"""

    code = "APPROVAL_TIMEOUT"


class RiskRejectedError(BusinessError):
    """风险规则命中被拒绝（供应商资质/追溯断裂/价格偏离等，开发文档 §6.4 L721-722）。"""

    code = "RISK_REJECTED"


class RateLimitError(BusinessError):
    """限流命中。"""

    code = "RATE_LIMITED"
