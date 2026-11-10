"""A5 LLMClient 单测（M3-01 验收：mock/llm 双模式，能力说明 §3）。

- mock 模式：零网络依赖返回占位 JSON；
- llm 模式：仅配置 MIIRP_LLM_API_KEY 且 base_url 非空时启用，
  缺端点/密钥抛 RuntimeError（明确失败，不静默）。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agents.llm_client as llm_mod
from app.agents.llm_client import LLMClient


def _run(coro):
    return asyncio.run(coro)


def test_mock_mode_returns_placeholder():
    client = LLMClient(mode="mock")
    out = _run(client.chat("system", "user"))
    assert "mock mode" in out


def test_llm_mode_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("MIIRP_LLM_API_KEY", raising=False)
    monkeypatch.setattr(llm_mod, "BASE_URL", "https://llm.example")
    client = LLMClient(mode="llm")
    with pytest.raises(RuntimeError, match="MIIRP_LLM_API_KEY"):
        _run(client.chat("s", "u"))


def test_llm_mode_missing_base_url_raises(monkeypatch):
    monkeypatch.setenv("MIIRP_LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_mod, "BASE_URL", "")
    client = LLMClient(mode="llm")
    with pytest.raises(RuntimeError, match="MIIRP_LLM_BASE_URL"):
        _run(client.chat("s", "u"))


def test_llm_mode_chat_success(monkeypatch):
    """llm 模式成功路径：POST /chat/completions 并返回 content（httpx mock，无网络）。"""
    monkeypatch.setenv("MIIRP_LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_mod, "BASE_URL", "https://llm.example")
    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": '{"decision": "approve"}'}}]}
    fake_client = AsyncMock()
    # async with 必须返回自身，否则 client.post 命中未配置的 child mock
    fake_client.__aenter__.return_value = fake_client
    fake_client.post.return_value = resp
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", lambda timeout: fake_client)

    client = LLMClient(mode="llm")
    out = _run(client.chat("sys", "usr"))
    assert '"approve"' in out
    fake_client.post.assert_awaited_once()
    args, kwargs = fake_client.post.await_args
    assert args[0] == "https://llm.example/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert kwargs["json"]["model"] == llm_mod.MODEL
