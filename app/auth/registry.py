"""身份与权限注册表：API Key → 角色/client_id；角色 → 工具白名单；审批角色白名单。

身份注入口径（api_contract §4/§8）：
- HTTP 模式：请求头 X-MIIRP-Key（auth.api_key_header 配置）→ API Key → 角色。
- stdio 模式：`MIIRP_ROLE` 环境变量注入角色（api_contract §8 口径 #5：
  勿被旧文档 docstring 的 MIIRP_CLIENT_ID 名误导，恒用 MIIRP_ROLE）。
- 未识别角色（role=None）→ resolve_client_id 兜底 "anonymous"（§6.7 on_request）。

白名单单一事实源（api_contract §4 规则：不得私自增减）：
- 角色 → 工具白名单：config/default.yaml `roles`（tools/list 裁剪依据），本模块只读引用。
- 审批角色白名单：config/default.yaml `approval.approve_roles`（api_contract §5）。

静态密钥表仅本地环境（开发文档 §6.7）；生产环境应替换为数据库/密钥管理服务。
"""
from __future__ import annotations

from app.config import APPROVE_ROLES, ROLES, tools_for_role

# 静态密钥表：角色 → API Key（仅本地环境；生产换数据库/密钥管理服务，开发文档 §6.7）
_API_KEYS: dict[str, str] = {
    "grn-operator": "key-grn-001",
    "iqc-inspector": "key-iqc-001",
    "warehouse-manager": "key-wh-001",
    "buyer": "key-buyer-001",
    "compliance-officer": "key-comp-001",
    "qty-check-agent": "key-qty-001",
    "price-check-agent": "key-price-001",
    "supplier-check-agent": "key-sup-001",
    "trace-check-agent": "key-trace-001",
    "review-agent": "key-review-001",
    "inventory-agent": "key-inv-001",
}

# 角色 → client_id（限流/审计主体，api_contract §4 身份与密钥表）
_CLIENT_IDS: dict[str, str] = {
    "grn-operator": "grn-001",
    "iqc-inspector": "iqc-001",
    "warehouse-manager": "wh-001",
    "buyer": "buyer-001",
    "compliance-officer": "comp-001",
    "qty-check-agent": "qty-001",
    "price-check-agent": "price-001",
    "supplier-check-agent": "sup-001",
    "trace-check-agent": "trace-001",
    "review-agent": "review-001",
    "inventory-agent": "inv-001",
}

# API Key → 角色 反向查表（与 _API_KEYS 一一对应，本地环境密钥无重复值）
_KEY_TO_ROLE: dict[str, str] = {k: r for r, k in _API_KEYS.items()}


class PermissionRegistry:
    """角色/密钥解析静态方法集（供 AuthRoleMiddleware 与 /approvals 路由复用，module_boundaries §5）。"""

    @staticmethod
    def tools_for(role: str) -> set[str]:
        """角色对应的工具白名单（最小可见，tools/list 裁剪依据）。"""
        return tools_for_role(role)

    @staticmethod
    def role_for_api_key(key: str) -> str | None:
        """API Key → 角色；未注册密钥返回 None（HTTP 模式鉴权）。"""
        return _KEY_TO_ROLE.get(key)

    @staticmethod
    def client_id_for_role(role: str) -> str | None:
        """角色 → client_id（限流/审计主体）；未注册角色返回 None。"""
        return _CLIENT_IDS.get(role)

    @staticmethod
    def resolve_client_id(role: str | None) -> str:
        """解析 client_id：角色未知（None）时兜底 "anonymous"（§6.7）。"""
        if role is None:
            return "anonymous"
        return _CLIENT_IDS.get(role, role)


# 导出常量：白名单单一事实源在 config/default.yaml，本模块只读引用不私自增减（api_contract §4/§5）
__all__ = ["APPROVE_ROLES", "ROLES", "PermissionRegistry"]
