"""LLM 客户端：OpenAI 兼容 chat/completions（经 httpx），支持 mock 离线模式。

模式判定（P2-1 统一）：is_llm_mode(mode) = mode != "mock"——mock 是唯一离线
模式，llm / openai-compatible（default.yaml agents.llm_mode 值域）均视为启用 LLM：
- mock 模式：不发起网络请求，返回占位 JSON（CI/演示/无 key 环境零依赖可运行）；
- LLM 模式：base_url 指向任意 OpenAI 兼容端点（OpenAI/DeepSeek/vLLM 等），
  API Key 仅从环境变量 MIIRP_LLM_API_KEY 注入（M3-01 验收：仅配置该变量时启用
  llm 调用），缺端点/密钥抛 RuntimeError（明确失败，不静默），不落代码。

模型无关设计：不绑定 SDK，仅依赖 HTTP 协议，便于切换厂商（能力说明 §3）。
"""
from __future__ import annotations

import os

import httpx

from app.config import AGENTS_CFG

MODE = AGENTS_CFG.get("llm_mode", "mock")
BASE_URL = AGENTS_CFG.get("llm_base_url", "").rstrip("/")
MODEL = AGENTS_CFG.get("llm_model", "your-llm-model")
TIMEOUT = float(AGENTS_CFG.get("llm_timeout_seconds", 30))

# 环境变量注入位：MIIRP_LLM_API_KEY（不在模块级读取，避免 import 期依赖环境）
_API_KEY_ENV = "MIIRP_LLM_API_KEY"


def is_llm_mode(mode: str) -> bool:
    """mode 是否表示启用 LLM 模式（非 mock 即 LLM，P2-1 统一判定）。

    配置值域：mock | openai-compatible（config/default.yaml agents.llm_mode）；
    历史兼容值 "llm" 亦按 LLM 处理。mock 是唯一离线模式：
    - mock → 上层走规则引擎，零网络依赖；
    - 其余（llm / openai-compatible / 未来厂商值）→ LLM 分支，需 MIIRP_LLM_API_KEY，
      缺密钥时 chat() 抛 RuntimeError，由上层兜底（专家 high / 复核 review）明确
      失败，绝不静默回退规则引擎（防漏放行语义不变）。
    """
    return mode != "mock"


class LLMClient:
    """OpenAI 兼容单轮对话客户端。

    mock/llm 双模式共用同一调用面（chat），上层 Agent 按 mode 分流：
    mock 模式直接走规则引擎，llm 模式经本客户端调用模型。
    """

    def __init__(self, mode: str = MODE):
        self.mode = mode

    async def chat(self, system: str, user: str, temperature: float = 0.0) -> str:
        """单轮对话返回文本。llm 模式缺端点/密钥时抛 RuntimeError（明确失败，不静默）。"""
        if not is_llm_mode(self.mode):
            return await self._mock_chat(system, user)
        api_key = os.environ.get(_API_KEY_ENV, "")
        if not BASE_URL or not api_key:
            raise RuntimeError(
                f"{_API_KEY_ENV} 或 MIIRP_LLM_BASE_URL 未配置，无法启用 llm 模式"
            )
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": MODEL, "temperature": temperature,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def _mock_chat(self, system: str, user: str) -> str:
        """离线 mock：返回占位 JSON。

        mock 模式下 agent 直接走规则引擎（review_rules），不经过 LLM 客户端；
        此方法仅兜底"直接调用 LLMClient"的场合（能力说明 §3）。
        """
        return '{"note": "mock mode: agent uses rule engine, LLM client not invoked"}'
