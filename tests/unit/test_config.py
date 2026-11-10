"""M1 配置分层测试（开发文档 §8.2、task_cards/M1.md M1-05 验收，D12/D31 回归）。

语义事实源（§6.1/§4.1、defect_regression D12/D31）：
- 三层合并：default.yaml → {env}.yaml → MIIRP_ 环境变量（最高优先级），深合并。
- 环境变量值经 _coerce 强转：bool → int → float → 原样字符串。
- 密钥/DSN 只经环境变量注入，yaml 不写明文、不做 $VAR 插值（D12）。
- DB_DSN 优先级：MIIRP_DB_DSN > yaml database.dsn 字面量。
- server.version 全项目统一 3.1.0（D31）。
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from app.config import (
    ROLES,
    SERVER_CFG,
    Settings,
    _coerce,
    _env_overrides,
    _load_file,
    load_config,
    tools_for_role,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
import app.config as config_mod


def test_default_config_has_roles(monkeypatch):
    """§8.2 锚点：roles 白名单含 create_inbound_record / approve_inbound_request。"""
    monkeypatch.delenv("MIIRP_ENV", raising=False)
    cfg = load_config("default")
    assert "roles" in cfg
    assert "create_inbound_record" in cfg["roles"]["review-agent"]
    assert "approve_inbound_request" in cfg["roles"]["warehouse-manager"]


def test_roles_constant_matches_config():
    """app.config.ROLES 与 load_config 结果一致（最小可见白名单）。"""
    assert "create_inbound_record" in ROLES["review-agent"]
    assert "approve_inbound_request" in ROLES["warehouse-manager"]
    assert "approve_inbound_request" in ROLES["compliance-officer"]


def test_env_override_int(monkeypatch):
    """§8.2 锚点：MIIRP_SERVER__PORT=9000 → 字符串强转为 int 9000。"""
    monkeypatch.setenv("MIIRP_SERVER__PORT", "9000")
    cfg = load_config("default")
    assert cfg["server"]["port"] == 9000
    assert isinstance(cfg["server"]["port"], int)


def test_env_override_bool(monkeypatch):
    """§8.2 锚点：MIIRP_METRICS__ENABLED=false → 强转为 bool False。"""
    monkeypatch.setenv("MIIRP_METRICS__ENABLED", "false")
    cfg = load_config("default")
    assert cfg["metrics"]["enabled"] is False


def test_env_overrides_mapping(monkeypatch):
    """_env_overrides：MIIRP_SERVER__PORT → {"server": {"port": ...}}（D12 键映射）。"""
    monkeypatch.setenv("MIIRP_SERVER__PORT", "9000")
    monkeypatch.setenv("MIIRP_METRICS__ENABLED", "false")
    over = _env_overrides()
    assert over["server"]["port"] == 9000
    assert over["metrics"]["enabled"] is False


def test_production_overrides_default():
    """二层覆盖：production.yaml 覆盖 default.yaml，未覆盖项保留 default。"""
    cfg = load_config("production")
    assert cfg["rate_limit"]["qps"] == 50          # production 覆盖 default 的 20
    assert cfg["audit"]["retention_days"] == 365   # production 覆盖 default 的 180
    assert cfg["server"]["port"] == 8000           # 未被覆盖项保留 default
    assert cfg["logging"]["json"] is True          # default 字段保留


def test_env_highest_priority_over_production(monkeypatch):
    """三层优先级：环境变量 > production.yaml > default.yaml。"""
    monkeypatch.setenv("MIIRP_RATE_LIMIT__QPS", "99")
    cfg = load_config("production")
    assert cfg["rate_limit"]["qps"] == 99          # env 覆盖 production 的 50
    assert cfg["audit"]["retention_days"] == 365   # production 覆盖 default 的 180
    assert cfg["server"]["port"] == 8000           # default 兜底


def test_coerce_types():
    """_coerce 强转顺序：bool → int → float → 原样字符串。"""
    assert _coerce("true") is True
    assert _coerce("FALSE") is False
    assert _coerce("9000") == 9000
    assert isinstance(_coerce("9000"), int)
    assert _coerce("3.14") == 3.14
    assert isinstance(_coerce("3.14"), float)
    assert _coerce("abc") == "abc"
    assert _coerce("") == ""


def test_db_dsn_env_overrides_yaml(monkeypatch):
    """D12 语义：DB_DSN 优先级 MIIRP_DB_DSN > yaml 字面量（sqlite:///data/miirp.db）。"""
    monkeypatch.setenv("MIIRP_DB_DSN", "postgresql://qa:pw@db:5432/miirp")
    monkeypatch.delenv("MIIRP_ENV", raising=False)
    try:
        reloaded = importlib.reload(config_mod)
        assert reloaded.DB_DSN == "postgresql://qa:pw@db:5432/miirp"
    finally:
        monkeypatch.undo()
        importlib.reload(config_mod)
    assert config_mod.DB_DSN == "sqlite:///data/miirp.db"


def test_production_dsn_not_plaintext():
    """D12：生产 DSN 不在 yaml 写明文，production.yaml database.dsn 为空串。"""
    data = _load_file(_ROOT / "config" / "production.yaml")
    assert data["database"]["dsn"] == ""


def test_yaml_no_var_interpolation():
    """D12：yaml 配置值不做 $VAR 插值（校验解析后的值，注释说明性文字不计）。"""
    for name in ("default.yaml", "production.yaml"):
        data = _load_file(_ROOT / "config" / name)

        def walk(node, path, name=name):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
            elif isinstance(node, str):
                assert not re.search(r"\$\{?\w+\}?", node), f"{name}{path} 含插值标记: {node!r}"

        walk(data, "")


def test_server_version_unified():
    """D31：server.version 全项目统一 3.1.0（load_config 与模块常量一致）。"""
    assert load_config("default")["server"]["version"] == "3.1.0"
    assert load_config("production")["server"]["version"] == "3.1.0"
    assert SERVER_CFG.get("version") == "3.1.0"


def test_production_without_dsn_fails_fast(monkeypatch):
    """P0-2 整改：MIIRP_ENV=production 且未注入 MIIRP_DB_DSN → 模块加载即抛 RuntimeError。

    生产数据安全红线：禁止静默回退本地 SQLite（原实现 dsn:"" 经 or 链回退）。
    """
    monkeypatch.setenv("MIIRP_ENV", "production")
    monkeypatch.delenv("MIIRP_DB_DSN", raising=False)
    try:
        with pytest.raises(RuntimeError, match="MIIRP_DB_DSN"):
            importlib.reload(config_mod)
    finally:
        monkeypatch.undo()
        importlib.reload(config_mod)
    assert config_mod.DB_DSN == "sqlite:///data/miirp.db"   # 恢复默认环境正常加载


def test_production_with_dsn_loads_ok(monkeypatch):
    """P0-2 整改：MIIRP_ENV=production 且注入 MIIRP_DB_DSN → 正常加载（不误伤）。"""
    monkeypatch.setenv("MIIRP_ENV", "production")
    monkeypatch.setenv("MIIRP_DB_DSN", "postgresql://qa:pw@db:5432/miirp")
    try:
        reloaded = importlib.reload(config_mod)
        assert reloaded.DB_DSN == "postgresql://qa:pw@db:5432/miirp"
    finally:
        monkeypatch.undo()
        importlib.reload(config_mod)


def test_secret_env_keys_excluded_from_config(monkeypatch):
    """P2-13 整改：密钥类环境变量（LLM API Key / DB DSN）不进配置字典。"""
    monkeypatch.setenv("MIIRP_LLM_API_KEY", "sk-secret-123")
    monkeypatch.setenv("MIIRP_DB_DSN", "postgresql://qa:pw@db:5432/miirp")
    over = _env_overrides()
    assert "llm" not in over and "LLM_API_KEY" not in over
    assert "db" not in over and "DB_DSN" not in over
    # 非密钥配置键仍正常映射
    monkeypatch.setenv("MIIRP_SERVER__PORT", "9000")
    over2 = _env_overrides()
    assert over2["server"]["port"] == 9000


def test_settings_load(monkeypatch):
    """Settings.load 封装返回三层合并结果。"""
    monkeypatch.delenv("MIIRP_ENV", raising=False)
    cfg = Settings().load("default")
    assert cfg["server"]["port"] == 8000
    assert "roles" in cfg


def test_tools_for_role():
    """tools_for_role：白名单映射与未知角色兜底空集合。"""
    assert "create_inbound_record" in tools_for_role("review-agent")
    assert tools_for_role("nobody") == set()
