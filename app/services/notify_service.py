"""通知服务（模拟）。

api_contract §2.9：notify 返回 {message_id, channel, receiver, title, status="sent"}；
message_id = uuid4().hex[:12]（与 §6.10 代码一致）。
"""
from __future__ import annotations

import uuid


class NotifyService:
    """站内消息/通知（模拟；生产可接 IM/邮件，接口不变）。"""

    @staticmethod
    async def send(channel: str, receiver: str, title: str, body: str) -> dict:
        """发送通知并返回消息回执（不落库，模拟外发）。"""
        return {"message_id": uuid.uuid4().hex[:12], "channel": channel,
                "receiver": receiver, "title": title, "status": "sent"}
