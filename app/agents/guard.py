"""工具调用安全护栏：LLM 输出（tool_call）在到达工具层前强制校验。

防御目标（能力说明 §6）：LLM 被 Prompt 注入诱导调用非授权工具 / 传入非法参数时，
在 Agent 侧拦截，避免直接命中工具层（纵深防御第二道闸，第一道为工具层 RBAC）。

- 工具白名单：P2-12 整改——单一数据源取自 config roles（app.config.ROLES），
  删除模块内硬编码 _SAFE 副本（旧实现与 config 双源分叉风险）；
- 参数形态校验：拒绝 list/dict 复杂参数（常见注入向量），只放行标量；
- 拒绝结果返回 "GUARD:" 前缀错误，进入审计留痕。
"""
from __future__ import annotations

from typing import Any

from app.config import ROLES


class ToolGuard:
    """Agent 侧工具调用白名单 + 参数形态校验（静态方法，无状态）。"""

    @staticmethod
    def check(role: str, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """校验 (role, tool_name, arguments) 是否放行；返回 (ok, 拒绝原因)。

        白名单唯一来源：config/default.yaml roles（P2-12 整改，与 AuthRoleMiddleware
        tools/list 裁剪 / tools/call 强制校验同源，消除双数据源分叉）。
        """
        allowed = set(ROLES.get(role, []))
        if tool_name not in allowed:
            return False, f"agent {role} 无权调用工具 {tool_name}"
        if not isinstance(arguments, dict):
            return False, "工具参数必须为对象"
        for key, value in arguments.items():
            if isinstance(value, (list, dict)) and value:
                return False, f"参数 {key} 含复杂对象，已拒绝"
        return True, ""
