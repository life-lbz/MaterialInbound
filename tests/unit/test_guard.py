"""A5 ToolGuard 单测（M3-01 验收：越权工具与复杂参数拦截，能力说明 §6）。

- 工具白名单：Agent 角色最小可见面（api_contract §4 逐字一致）；
  非 Agent 角色回退 ROLES 配置兜底。
- 参数形态校验：拒绝 list/dict 复杂参数（注入向量），只放行标量；
  空集合（[]/{}）不视为复杂对象。
"""
from __future__ import annotations

from app.agents.guard import ToolGuard


def test_guard_allows_scalar_args():
    ok, why = ToolGuard.check("qty-check-agent", "get_po", {"po_no": "PO-1"})
    assert ok is True
    assert why == ""


def test_guard_blocks_unauthorized_tool():
    ok, why = ToolGuard.check("qty-check-agent", "approve_inbound_request", {})
    assert ok is False
    assert "无权调用" in why


def test_guard_blocks_non_dict_args():
    ok, why = ToolGuard.check("qty-check-agent", "get_po", ["PO-1"])
    assert ok is False
    assert "必须为对象" in why


def test_guard_blocks_nested_dict_arg():
    ok, why = ToolGuard.check("qty-check-agent", "get_po", {"po_no": {"nested": 1}})
    assert ok is False
    assert "复杂对象" in why


def test_guard_blocks_list_arg():
    ok, why = ToolGuard.check("qty-check-agent", "get_po", {"po_no": ["PO-1"]})
    assert ok is False
    assert "复杂对象" in why


def test_guard_allows_empty_collections():
    """空 list/dict 视为标量放行（不误伤正常空参数）。"""
    ok, why = ToolGuard.check("qty-check-agent", "get_po", {"tags": []})
    assert ok is True
    assert why == ""


def test_guard_fallback_role_config_allowed():
    """非 Agent 角色（无 _SAFE 条目）回退 ROLES 白名单。"""
    ok, why = ToolGuard.check("warehouse-manager", "get_inventory", {"sku": "M-1001"})
    assert ok is True
    assert why == ""


def test_guard_fallback_role_config_denied():
    ok, why = ToolGuard.check("warehouse-manager", "get_po", {})
    assert ok is False
    assert "无权调用" in why


def test_guard_unknown_role_denied():
    ok, why = ToolGuard.check("unknown-role", "get_po", {})
    assert ok is False
    assert "无权调用" in why
