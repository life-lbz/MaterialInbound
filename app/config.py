"""分层配置：default.yaml → {env}.yaml → 环境变量（MIIRP_ 前缀，最高优先级）。

约定（开发文档 §4.1/§6.1）：
- 密钥与连接串只经环境变量注入，yaml 中不写 $VAR 插值（不做插值，D12）。
- 环境变量键 MIIRP_SERVER__PORT=9000 → {"server": {"port": 9000}}，值经 _coerce 强转类型。
- 模块加载时即完成分层合并（_config = load_config()），常量供各模块直接引用。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_ENV_PREFIX = "MIIRP_"

# 密钥类环境变量不进配置字典（P2-13 整改）：LLM API Key / 数据库 DSN 由专用读取点消费，
# 防止密钥残留内存配置字典（DB_DSN 在下方单独读取，MIIRP_LLM_API_KEY 由 llm_client 读取）。
_SECRET_ENV_KEYS: frozenset[str] = frozenset({"MIIRP_LLM_API_KEY", "MIIRP_DB_DSN"})


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并 override 到 base（返回 base 本身，就地修改）。"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _load_file(path: Path) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _coerce(raw: str) -> Any:
    """环境变量字符串强转：bool → int → float → 原样字符串。"""
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _env_overrides(prefix: str = _ENV_PREFIX) -> dict:
    """MIIRP_SERVER__PORT=8001 → {"server": {"port": 8001}}（D12）。

    密钥类键（_SECRET_ENV_KEYS）跳过，避免密钥残留配置字典（P2-13 整改）。
    """
    out: dict = {}
    for key, raw in os.environ.items():
        if not key.startswith(prefix):
            continue
        if key.upper() in _SECRET_ENV_KEYS:
            continue
        parts = key[len(prefix):].lower().split("__")
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = _coerce(raw)
    return out


def load_config(env: str | None = None) -> dict:
    """加载分层配置：default.yaml → {env}.yaml → MIIRP_ 环境变量（最高优先级）。"""
    env = env or os.environ.get("MIIRP_ENV", "default")
    cfg = _load_file(_ROOT / "config" / "default.yaml")
    if env != "default":
        cfg = _deep_merge(cfg, _load_file(_ROOT / "config" / f"{env}.yaml"))
    return _deep_merge(cfg, _env_overrides())


class Settings:
    """配置加载封装：load() 返回 default → {env}.yaml → 环境变量合并后的完整配置字典。"""

    def load(self, env: str | None = None) -> dict:
        return load_config(env)


_APP_ENV = os.environ.get("MIIRP_ENV", "default")
_config = load_config(_APP_ENV)

SERVER_CFG = _config.get("server", {})
LOG_CFG = _config.get("logging", {})
AUTH_CFG = _config.get("auth", {})
RATE_LIMIT_CFG = _config.get("rate_limit", {})
ROLES: dict[str, list[str]] = _config.get("roles", {})
APPROVAL_CFG = _config.get("approval", {})
WRITE_TOOLS: set[str] = set(APPROVAL_CFG.get("write_tools", []))
# 审批角色权限由 ROLES 白名单控制（仅授权角色可见 approve_inbound_request 工具）；
# approve_roles 保留为策略声明与审计参考
APPROVE_ROLES: set[str] = set(APPROVAL_CFG.get("approve_roles", []))
HIGH_VALUE_THRESHOLD_CNY: float = APPROVAL_CFG.get("high_value_threshold_cny", 100000.0)
APPROVAL_TIMEOUT: float = APPROVAL_CFG.get("timeout_seconds", 120)
# 生产 DSN 统一由环境变量提供（字面量，无插值）：MIIRP_DB_DSN > yaml database.dsn 字面量
DB_DSN: str = (os.environ.get("MIIRP_DB_DSN")
               or _config.get("database", {}).get("dsn", "")
               or "")
# P0-2 整改：生产环境 DSN 缺失必须 fail-fast，禁止静默回退本地 SQLite（数据安全红线）
if _APP_ENV == "production" and not DB_DSN:
    raise RuntimeError(
        "MIIRP_ENV=production 时 MIIRP_DB_DSN 必须显式注入，"
        "禁止回退本地 SQLite（P0-2 整改）"
    )
AUDIT_RETENTION_DAYS: int = _config.get("audit", {}).get("retention_days", 180)
METRICS_ENABLED: bool = _config.get("metrics", {}).get("enabled", True)
# P3 整改：/metrics 端点鉴权开关（production.yaml 开启，default 关闭兼容本地抓取）
METRICS_AUTH: bool = bool(_config.get("metrics", {}).get("auth", False))
LATENCY_BUCKETS_MS: list[float] = [float(x) for x in _config.get("metrics", {}).get("latency_buckets_ms", [5, 10, 25, 50, 100, 250, 500, 1000, 2500])]
METRICS_WINDOW: int = int(_config.get("metrics", {}).get("window_size", 5000))
MAX_BODY_BYTES: int = int(SERVER_CFG.get("max_body_bytes", 1048576))
AGENTS_CFG = _config.get("agents", {})


def tools_for_role(role: str) -> set[str]:
    """返回角色对应的工具白名单（最小可见）。"""
    return set(ROLES.get(role, []))
