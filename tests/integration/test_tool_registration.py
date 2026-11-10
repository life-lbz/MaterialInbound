"""工具注册与签名核对（api_contract §2 签名契约、§1 工具总数口径）。

语义事实源：
- 全量 9 个工具文件、16 个工具（api_contract §1，P3-1 口径）。
- 工具名/入参名/返回键名与 api_contract §2 逐字一致（协作方案 §3.5 规则规范）。
- 每模块提供 register(mcp: FastMCP) -> None（§6.8 开头约定）。
- ctx 为框架注入参数（Context），不进入参数 schema（api_contract §2.7 备注）。
"""
from __future__ import annotations

import json

import pytest

# 契约工具名 → 入参名（api_contract §2；ctx 为框架注入非客户端入参）
_CONTRACT: dict[str, set[str]] = {
    "get_po": {"po_no"},
    "create_po": {"pr_no", "sku", "quantity", "unit_price", "currency", "supplier_id"},
    "get_grn": {"grn_no"},
    "create_grn": {"po_no", "sku", "received_qty", "batch_no", "carrier"},
    "get_iqc": {"iqc_no"},
    "create_iqc_report": {"grn_no", "sku", "sample_qty", "defect_qty"},
    "get_inventory": {"sku"},
    "list_low_stock_materials": set(),
    "get_stock_usage": {"sku", "days"},
    "get_supplier_profile": {"supplier_id"},
    "get_supplier_quote": {"supplier_id", "sku"},
    "get_trace_batch": {"batch_no"},
    "create_inbound_record": {"grn_no", "iqc_no", "sku", "quantity",
                              "unit_price", "currency", "supplier_id"},
    "approve_inbound_request": {"request_id", "approved", "reason"},
    "list_pending_approvals": set(),
    "notify": {"channel", "receiver", "title", "body"},
}

_CONTRACT_FILES = (
    "po_tools", "grn_tools", "iqc_tools", "inventory_tools",
    "supplier_tools", "trace_tools", "inbound_tools",
    "approval_tools", "notify_tools",
)


def _params(tool) -> set[str]:
    return set(tool.parameters.get("properties", {}).keys())


def _data(res):
    """从 ToolResult 提取结构化数据（structuredContent 优先，回退 JSON 文本解析）。"""
    if getattr(res, "structuredContent", None):
        return res.structuredContent
    return json.loads(res.content[0].text)


@pytest.fixture(autouse=True)
def _reset_inventory_seed():
    """重置库存种子（P0-4 整改适配）：in-process 集成测试中入库过账（restock）
    会改变库存台账，本模块用例依赖种子"M-1001 低于安全线"的断言须恢复种子状态。"""
    import app.services.inventory_service as inv_mod
    inv_mod._STOCK.clear()
    inv_mod._seed()
    yield
    inv_mod._STOCK.clear()
    inv_mod._seed()


@pytest.mark.asyncio
async def test_nine_modules_sixteen_tools_registered(mcp_app):
    """9 工具文件、16 工具全部注册（api_contract §1 口径）。"""
    names = {t.name for t in await mcp_app.list_tools(run_middleware=False)}
    assert names == set(_CONTRACT.keys())
    assert len(names) == 16
    assert len(_CONTRACT_FILES) == 9


@pytest.mark.asyncio
async def test_tool_signatures_match_contract(mcp_app):
    """工具入参名与 api_contract §2 逐字一致（协作方案 §3.5 规则）。"""
    tools = {t.name: t for t in await mcp_app.list_tools(run_middleware=False)}
    for name, expect_params in _CONTRACT.items():
        assert name in tools, f"缺失契约工具 {name}"
        assert _params(tools[name]) == expect_params, \
            f"{name} 入参与契约不符: {_params(tools[name])} != {expect_params}"


def test_each_module_has_register(mcp_app):
    """9 模块均提供 register(mcp) -> None（server.py 装配位契约，module_boundaries §5）。"""
    import importlib


    for mod_name in _CONTRACT_FILES:
        mod = importlib.import_module(f"app.tools.{mod_name}")
        assert callable(getattr(mod, "register", None)), f"{mod_name} 缺 register"


@pytest.mark.asyncio
async def test_read_tools_return_contract_keys(mcp_app, monkeypatch):
    """读工具返回键名与 api_contract §2 一致（种子数据实测，data_contract §4）。

    按角色分配合法调用组合（api_contract §4 双重拦截、RV-M2 P0-1 修复后语义）：
    P0-1 后 tools/call 按角色白名单强制校验，单一角色无法再越权调用全部读工具，
    故每个工具以拥有该工具白名单权限的角色身份调用（角色-工具矩阵见 §4）；
    get_iqc 不在任何角色白名单（§4 矩阵现状），无合法 tools/call 路径，返回键
    契约由 data_contract §2.3 承载，其白名单事实由 test_get_iqc_no_role_in_whitelist 锚定。
    """
    cases = [
        # (角色, 工具, 入参, 期望返回键) —— 角色均拥有该工具白名单权限（§4）
        ("grn-operator", "get_po", {"po_no": "PO-20260818001"},
         {"po_no", "pr_no", "sku", "quantity", "unit_price", "currency", "supplier_id", "status"}),
        ("qty-check-agent", "get_grn", {"grn_no": "GRN-20260820-011"},
         {"grn_no", "po_no", "sku", "received_qty", "batch_no", "carrier", "received_at"}),
        ("warehouse-manager", "get_inventory", {"sku": "M-1001"},
         {"sku", "stock_qty", "safety_stock", "reorder_point", "avg_daily_usage", "last_90d_usage"}),
        ("inventory-agent", "get_stock_usage", {"sku": "M-1001"},
         {"sku", "days", "avg_daily_usage", "total_usage"}),
        ("supplier-check-agent", "get_supplier_profile", {"supplier_id": "SUP-01"},
         {"supplier_id", "name", "region", "qualification_expiry", "risk_level", "ontime_rate", "pass_rate"}),
        ("price-check-agent", "get_supplier_quote", {"supplier_id": "SUP-01", "sku": "M-1001"},
         {"supplier_id", "sku", "latest_price", "currency", "p90_min", "p90_max"}),
        ("trace-check-agent", "get_trace_batch", {"batch_no": "B20260820011"},
         {"batch_no", "supplier_batch", "production_date", "material_lot", "trace_complete"}),
    ]
    for role, name, args, expect_keys in cases:
        monkeypatch.setenv("MIIRP_ROLE", role)
        res = await mcp_app.call_tool(name, args)
        data = _data(res)
        assert isinstance(data, dict) and expect_keys <= set(data.keys()), \
            f"{name}({role}) 返回键不符: {data}"


def test_get_iqc_no_role_in_whitelist():
    """契约事实锚点：get_iqc 不在任何角色白名单（api_contract §4 矩阵现状，RV-M0 认可）。

    P0-1 双重拦截下，未授权工具无合法 tools/call 路径；本断言固定 §4 矩阵现状，
    防止白名单配置漂移导致"越权可达"悄然回归（若未来契约授权 get_iqc，
    需同步更新本用例与 data_contract §2.3 返回键锚点）。
    """
    from app.config import ROLES

    authorized = {tool for tools in ROLES.values() for tool in tools}
    assert "get_iqc" not in authorized


@pytest.mark.asyncio
async def test_list_low_stock_materials_returns_list(mcp_app, monkeypatch):
    """list_low_stock_materials 返回低库存列表（种子 M-1001 低于安全线，data_contract §4）。"""
    monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
    res = await mcp_app.call_tool("list_low_stock_materials", {})
    data = _data(res)
    assert isinstance(data, list)
    assert any(row["sku"] == "M-1001" for row in data)


@pytest.mark.asyncio
async def test_notify_returns_contract_keys(mcp_app, monkeypatch):
    """notify 返回 {message_id, channel, receiver, title, status}（api_contract §2.9）。"""
    monkeypatch.setenv("MIIRP_ROLE", "warehouse-manager")
    res = await mcp_app.call_tool("notify", {"channel": "internal", "receiver": "wh-001",
                                             "title": "t", "body": "b"})
    data = _data(res)
    assert {"message_id", "channel", "receiver", "title", "status"} <= set(data.keys())
    assert data["status"] == "sent"
