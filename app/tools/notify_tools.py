"""通知工具：站内消息/通知（模拟，生产可接 IM/邮件）（api_contract §2.9）。

角色白名单：warehouse-manager、buyer、compliance-officer、review-agent、inventory-agent。
"""
from __future__ import annotations

from fastmcp import FastMCP

from app.services.notify_service import NotifyService


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def notify(channel: str, receiver: str, title: str, body: str) -> dict:
        """发送通知。参数 channel: 通道（internal/email/im）；receiver: 接收方；
        title: 标题；body: 正文。"""
        return await NotifyService.send(channel=channel, receiver=receiver,
                                        title=title, body=body)
