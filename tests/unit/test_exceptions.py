"""业务异常体系测试（开发文档 §6.4，M1-02 验收）。

语义（§6.4）：所有业务异常继承 BusinessError，携带对外可见的稳定错误码；
RiskRejectedError.code == "RISK_REJECTED"（§6.4 风险规则命中拒绝）；
detail 为内部细节，不入对外响应（由上层 mask_error_details 控制）。
"""
from __future__ import annotations

import pytest

from app.exceptions import (
    ApprovalRejectedError,
    ApprovalTimeoutError,
    BusinessError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    RiskRejectedError,
)

ALL_EXCEPTIONS = (
    NotFoundError,
    PermissionDeniedError,
    ApprovalRejectedError,
    ApprovalTimeoutError,
    RiskRejectedError,
    RateLimitError,
)


def test_risk_rejected_code():
    """§6.4 锚点：RiskRejectedError.code == RISK_REJECTED。"""
    assert RiskRejectedError.code == "RISK_REJECTED"


def test_all_stable_codes():
    """全体系稳定错误码。"""
    assert BusinessError.code == "BUSINESS_ERROR"
    assert NotFoundError.code == "NOT_FOUND"
    assert PermissionDeniedError.code == "PERMISSION_DENIED"
    assert ApprovalRejectedError.code == "APPROVAL_REJECTED"
    assert ApprovalTimeoutError.code == "APPROVAL_TIMEOUT"
    assert RateLimitError.code == "RATE_LIMITED"


def test_hierarchy_all_derive_from_business_error():
    """所有业务异常均为 BusinessError 子类（统一转译 MCP ToolError）。"""
    for cls in ALL_EXCEPTIONS:
        assert issubclass(cls, BusinessError)
    assert issubclass(BusinessError, Exception)


def test_message_and_detail():
    """message 进 str() 且自带业务码前缀（P2-4 整改）；detail 为内部细节（独立属性，不入 str）。"""
    err = BusinessError("出错了", detail="内部堆栈信息")
    assert str(err) == "BUSINESS_ERROR: 出错了"     # P2-4：跨框架层透传的业务码前缀
    assert err.message == "BUSINESS_ERROR: 出错了"
    assert err.raw_message == "出错了"              # 原始文案保留（内部引用）
    assert err.detail == "内部堆栈信息"


def test_detail_default_none():
    """未传 detail 时默认为 None。"""
    err = RiskRejectedError("供应商资质过期")
    assert err.message == "RISK_REJECTED: 供应商资质过期"   # P2-4：业务码前缀
    assert err.raw_message == "供应商资质过期"
    assert err.detail is None


def test_raise_as_business_error():
    """RiskRejectedError 可按 BusinessError 捕获（调用方可泛化处理）。"""
    with pytest.raises(BusinessError) as exc_info:
        raise RiskRejectedError("价格偏离超阈值")
    assert exc_info.value.code == "RISK_REJECTED"
