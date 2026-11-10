"""Agent 编排层子包（能力说明 §3-§8，A5 所有权，M3-01/02）。

提供：
- llm_client：OpenAI 兼容 chat/completions 客户端（mock/llm 双模式，§3）；
- prompts：四路专家 + 合规复核 + 库存预警系统提示词（§4）；
- review_rules：确定性审查规则引擎（qty/price/supplier/trace/compliance，§5）；
- guard：ToolGuard 工具白名单 + 参数形态校验（§6）；
- review_agent：多 Agent 入库审查编排（四路专家并行 + 合规复核，§7）；
- inventory_agent：单 Agent 安全库存预警与再订货建议（§8）。

边界（module_boundaries §4）：本层不得直接访问数据库，一律经 call_tool 回调
调用工具层；不反向依赖 app/tools、app/middleware、app/services（评测 _fake_call 除外）。
"""
