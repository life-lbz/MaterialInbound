"""Agent 版入库审查演示：多 Agent 编排（ReviewAgent + InventoryAgent）。

能力说明 §10.2；依赖 A5 交付的 app/agents/review_agent.py 与 inventory_agent.py
（M3-02，联调时序由 A0 调度确认——A5 未交付时本脚本报告阻塞点并以退出码 3 退出，
脚本已按接口契约编码，A5 交付后即可联调）。

演示内容：
1. ReviewAgent.review：四路专家（数量/价格/供应商/追溯）asyncio.gather 并行采集证据
   → 合规复核汇总，输出 {correlation_id, decision, risks, details}（api_contract §7）；
   审查 GRN-20260820-011（mock 规则引擎，能力说明 §9.1 c01）→ decision=approve。
2. 决策 approve → review-agent 提交入库 → warehouse-manager 审批 → 过账，
   展示"Agent 建议 → 审批事件 → 审计记录"全链路留痕（correlation_id）。
3. InventoryAgent.check：单 Agent 库存预警（M-1001 库存 12000 < 安全线 15000 → alert=True，
   建议订货量 = avg_daily_usage×replenish_days + (safety_stock - stock_qty) 向上取整到 100 的倍数
   = 620×14 + 3000 = 11680 → 11700，能力说明 §8）。

模式：默认 mock（config agents.llm_mode=mock）离线可跑；LLM 模式仅在配置
MIIRP_LLM_API_KEY 且 llm_mode=llm/openai-compatible 时启用（任务卡 M3-04 规则）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import AGENTS_CFG
from client._approval_flow import submit_and_approve
from client.mcp_client import MCPClient

# 审查对象（PASS 锚点，能力说明 §9.1 c01 / §10.2）
REVIEW_GRN = {"po_no": "PO-20260818001", "grn_no": "GRN-20260820-011",
              "iqc_no": "IQC-20260820-011", "sku": "M-1001", "supplier_id": "SUP-02",
              "batch_no": "B20260820011", "quantity": 50000,
              "unit_price": 0.0071, "currency": "USD"}

_AGENT_ROLES = ("qty-check-agent", "price-check-agent", "supplier-check-agent",
                "trace-check-agent", "inventory-agent", "review-agent",
                "warehouse-manager")


def _load_agents():
    """加载 A5 交付的 Agent 编排模块（M3-02 交付物）。

    A5 未交付时报告阻塞点并以退出码 3 退出（联调时序由 A0 调度确认，
    RV-M0-契约复审-002；脚本已按能力说明 §10.2 接口契约编码）。
    """
    try:
        from app.agents.inventory_agent import InventoryAgent
        from app.agents.review_agent import ReviewAgent
        return ReviewAgent, InventoryAgent
    except ImportError as exc:
        print("[阻塞] app/agents 未交付（A5 M3-01/M3-02 pending），Agent 版演示无法联调。")
        print(f"       原因: {exc}")
        print("       状态: 脚本已按能力说明 §10.2 接口契约编码完成；")
        print("             待 A5 交付后由 A0 调度联调（M3-04 depends_on 应含 M3-02）。")
        raise SystemExit(3) from exc


async def _make_clients(roles: tuple[str, ...]) -> dict[str, MCPClient]:
    """顺序建立角色会话（每个角色最小可见面），返回 role → MCPClient。

    stdio_client 的 cancel scope 须在进入它的任务上下文退出；asyncio.gather 并行
    __aenter__ 会在 gather 子协程中进入、main 协程中退出，触发 anyio
    "cancel scope in a different task" 错误（联调实测），故与退出一样采用顺序建立。
    """
    clients = [MCPClient(r) for r in roles]
    for client in clients:
        await client.__aenter__()
    return {c.role: c for c in clients}


async def _close_clients(clients: dict[str, MCPClient]) -> None:
    """逆序退出全部角色会话（后进先出，满足 anyio cancel scope LIFO 约束）。

    stdio_client 的 cancel scope 须在同一任务内按"后进先出"退出（RV-M3 P1-1）：
    - _make_clients 顺序进入（先进先出），退出必须逆序（后进先出），
      否则触发 RuntimeError "Attempted to exit a cancel scope in a different
      order than it was entered"，脚本以 exit 1 收尾（M3-04 离线可运行不达标）；
    - 参考 demo_inbound_review_rules.py async with 多上下文管理器（后进先出）
      退出成功模式；
    - 单会话退出异常仅告警不阻断，确保任一会话退出失败不影响整体清理。
    """
    for client in reversed(list(clients.values())):
        try:
            await client.__aexit__(None, None, None)
        except Exception as exc:  # noqa: BLE001 - 清理阶段容错：单会话退出失败不阻断整体退出
            print(f"  [清理] 会话退出失败（{client.role}）: {exc!r}，继续清理其余会话")


def _build_dispatch(clients: dict[str, MCPClient]):
    """构造 Agent 回调：按角色分发到对应会话（Agent 不直接访问数据库，经 call_tool）。"""
    async def _call(role: str, tool: str, args: dict) -> dict:
        return await clients[role].call_tool(tool, args)
    return _call


def _llm_enabled() -> bool:
    """LLM 模式仅在配置 MIIRP_LLM_API_KEY 且 llm_mode=llm/openai-compatible 时启用。"""
    return bool(os.environ.get("MIIRP_LLM_API_KEY")) and \
        AGENTS_CFG.get("llm_mode") in ("llm", "openai-compatible")


async def main() -> None:
    ReviewAgent, InventoryAgent = _load_agents()
    llm_mode = "llm（已配置 MIIRP_LLM_API_KEY）" if _llm_enabled() else "mock（离线，无外部模型依赖）"
    print("=" * 74)
    print("MIIRP Agent 版入库审查演示（agent_inbound_review.py）")
    print("=" * 74)
    print(f"Agent 模式: {llm_mode}")

    clients = await _make_clients(_AGENT_ROLES)
    try:
        dispatch = _build_dispatch(clients)
        agent_mode = "llm" if _llm_enabled() else "mock"

        review_agent = ReviewAgent(call_tool=dispatch)
        review_agent.llm.mode = agent_mode
        result = await review_agent.review(
            po_no=REVIEW_GRN["po_no"], grn_no=REVIEW_GRN["grn_no"],
            iqc_no=REVIEW_GRN["iqc_no"], sku=REVIEW_GRN["sku"],
            supplier_id=REVIEW_GRN["supplier_id"], batch_no=REVIEW_GRN["batch_no"],
            quantity=REVIEW_GRN["quantity"], unit_price=REVIEW_GRN["unit_price"],
            currency=REVIEW_GRN["currency"])

        print("\n== 一、四路专家并行审查（asyncio.gather，能力说明 §7）==")
        for name, detail in result["details"].items():
            print(f"  - {name}专家: {json.dumps(detail, ensure_ascii=False)}")
        print(f"\n  风险结论: {json.dumps(result['risks'], ensure_ascii=False)}")
        print(f"  合规决策: {result['decision']}")
        print(f"  Agent 决策留痕 correlation_id: {result['correlation_id']}")
        if result["decision"] != "approve":
            raise RuntimeError(f"Agent 审查未按预期得到 approve，实得 {result['decision']}")

        print("\n== 二、Agent 建议 → 审批 → 审计 全链路 ==")
        flow = await submit_and_approve(REVIEW_GRN, clients["review-agent"],
                                        clients["warehouse-manager"])
        event = flow["approval_event"]
        print(f"  [审批事件] request_id={event['id']}")
        print(f"              correlation_id={event['correlation_id']}"
              "（服务端请求链路，与审计中间件同算法）")
        print(f"              proposer={event['proposer']} amount_cny={event['amount_cny']}"
              f" double_review={event['double_review']}")
        print(f"  [审批决策] {json.dumps(flow['decision'], ensure_ascii=False)}")
        print(f"  [过账/审计留痕] {json.dumps(flow['inbound'], ensure_ascii=False)}")
        print("              入库单 approval_request_id 关联审批单（选项 B，D1 回归），")
        print("              审计事件与审批单以 correlation_id 三向闭环（开发文档 §2.5）")

        print("\n== 三、单 Agent 库存预警（InventoryAgent，能力说明 §8）==")
        inventory_agent = InventoryAgent(call_tool=dispatch)
        inventory_agent.llm.mode = agent_mode
        alert = await inventory_agent.check("M-1001", replenish_days=14)
        print(f"  {json.dumps(alert, ensure_ascii=False)}")
        if not alert.get("alert"):
            raise RuntimeError("库存预警未按预期触发（M-1001 12000 < 安全线 15000）")
        if alert.get("suggested_qty") != 11700:
            raise RuntimeError(f"建议订货量未按预期（期望 11700，实得 {alert.get('suggested_qty')}）")

        print("\n== 结论：Agent 版演示通过（四路证据 / 合规决策 / 审批 / 审计留痕齐备）==")
    finally:
        await _close_clients(clients)


if __name__ == "__main__":
    asyncio.run(main())
