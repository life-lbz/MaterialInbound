"""工具层客户端：按角色身份连接（stdio），支持 list_tools / call_tool。

api_contract §6（客户端契约，D16/D28 回归）：
- connect(role)：stdio 拉起 server.py，env 注入 MIIRP_ROLE=role
  （api_contract §4 身份与密钥表；§8 口径 #5 恒用 MIIRP_ROLE，
  勿被旧文档 docstring 的 MIIRP_CLIENT_ID 名误导）。
- call_tool 结果解析统一 structuredContent / JSON（D16）：
  1) result.isError 为 True → 抛 RuntimeError（D28：携带 content[0].text，
     不做 json.loads，避免对非 JSON 错误文本崩溃）；
  2) result.structuredContent 存在 → 直接返回（优先）；
  3) 否则 content[0].text 用 json.loads 解析；
  4) 兜底返回 {"raw": str(result)}（防脆弱文本截取）。

实现说明：
- 角色白名单（config/default.yaml roles）即最小可见边界；客户端按角色身份连接，
  不传入超管身份（协作方案 §3.7 最小权限）。
- spawn 命令用 sys.executable（当前解释器，conda 环境下保证依赖可见）；
  connect 显式传 --transport stdio——default.yaml server.transport=streamable-http，
  若照抄契约 §6.11 示例（args=[SERVER_CMD]）子进程会误走 HTTP 分支导致握手失败，
  此处以可运行为准并加参数（契约示例与当前配置存在偏差，A10 复审时对齐）。
- 日志抑制（联调已知项）：app/logging_setup.py 的 handler 绑定 sys.stdout
  （开发文档 §6.2，生产 HTTP 容器采集口径），stdio 模式下会污染 JSON-RPC 协议流，
  使客户端握手/解析失败。客户端注入 MIIRP_LOGGING__LEVEL=ERROR（config 环境变量
  覆盖，D12）把服务端日志抑制到 ERROR 级，正常流程 stdout 仅承载协议。
  服务端修复建议：stdio 模式日志切 stderr（A2），修复后移除本抑制。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Self

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_CMD = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "server.py"))


def _parse_result(result, name: str) -> dict:
    """统一结果解析（D16/D28）：isError → RuntimeError；structuredContent 优先；回退 JSON。"""
    if result.isError:
        text = result.content[0].text if result.content else f"tool call failed: {name}"
        raise RuntimeError(text)
    if result.structuredContent:
        return result.structuredContent
    if result.content and result.content[0].text:
        return json.loads(result.content[0].text)
    return {"raw": str(result)}


async def connect(role: str) -> tuple[ClientSession, object]:
    """以角色身份建立 stdio 连接：env 注入 MIIRP_ROLE，返回 (session, transport)。

    transport 为 stdio_client 的 async context manager（含 read/write 流与子进程），
    由调用方负责退出（契约 §6.11 示例把 yield 的 (read, write) 元组当 context
    manager 退出会 AttributeError，此处修正：返回 transport 本体，其 __aexit__ 终止子进程）。
    """
    env = dict(os.environ)
    env["MIIRP_ROLE"] = role          # stdio 模式角色经环境变量注入（auth_role.py）
    env["MIIRP_LOGGING__LEVEL"] = "ERROR"   # 抑制服务端 stdout 日志污染协议流（见模块 docstring）
    params = StdioServerParameters(command=sys.executable,
                                   args=[SERVER_CMD, "--transport", "stdio"], env=env)
    transport = stdio_client(params)
    read, write = await transport.__aenter__()
    session = await ClientSession(read, write).__aenter__()
    await session.initialize()
    return session, transport


async def list_tools(role: str):
    """按角色列出可见工具（tools/list 最小可见裁剪结果，api_contract §4）。"""
    session, transport = await connect(role)
    try:
        return (await session.list_tools()).tools
    finally:
        await session.__aexit__(None, None, None)
        await transport.__aexit__(None, None, None)


async def call_tool(role: str, name: str, arguments: dict) -> dict:
    """以指定角色调用工具，返回结构化结果（structuredContent/JSON 统一解析）。

    越权工具名在服务端 tools/call 阶段被拒（PermissionDeniedError → isError 文本）。
    """
    session, transport = await connect(role)
    try:
        result = await session.call_tool(name, arguments)
    finally:
        await session.__aexit__(None, None, None)
        await transport.__aexit__(None, None, None)
    return _parse_result(result, name)


class MCPClient:
    """复用会话的角色客户端：一次连接多次调用（演示脚本高效路径）。

    与顶层 connect/list_tools/call_tool 同解析逻辑；角色身份在 connect 时固定。
    用法：
        async with MCPClient("warehouse-manager") as client:
            tools = await client.list_tools()
            data = await client.call_tool("get_inventory", {"sku": "M-1001"})
    """

    def __init__(self, role: str):
        self.role = role
        self._session: ClientSession | None = None
        self._transport: object | None = None

    async def __aenter__(self) -> Self:
        self._session, self._transport = await connect(self.role)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.__aexit__(exc_type, exc, tb)
        if self._transport is not None:
            await self._transport.__aexit__(exc_type, exc, tb)
        self._session = self._transport = None

    async def list_tools(self):
        if self._session is None:
            raise RuntimeError("MCPClient 未连接（请用 async with 进入上下文）")
        return (await self._session.list_tools()).tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if self._session is None:
            raise RuntimeError("MCPClient 未连接（请用 async with 进入上下文）")
        result = await self._session.call_tool(name, arguments)
        return _parse_result(result, name)


__all__ = ["SERVER_CMD", "MCPClient", "call_tool", "connect", "list_tools"]
