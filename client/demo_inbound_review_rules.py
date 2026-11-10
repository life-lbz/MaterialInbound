"""规则版入库审查演示（无 LLM 依赖）：最小可见 + 四路审查 + 审批 + 审计留痕。

双路径（能力说明 §10.1、defect_regression D4）：
- PASS 路径：GRN-20260820-011（实收 50000 无超收、SUP-02 正常、追溯完整）
  → 四路全低风险 → approve → review-agent 提交入库 → warehouse-manager 审批 → 过账。
- FAIL 路径：GRN-20260820-012（实收 54000 超收 8%、SUP-01 高风险、追溯断裂）
  → 数量/供应商/追溯高风险 → reject → 不创建入库单。

演示要点：
- 最小权限（协作方案 §3.7）：每路证据用对应专家角色（qty/price/supplier/trace-check-agent），
  提交用 review-agent，审批用 warehouse-manager，不使用超管身份。
- 审批留痕：审批待办携带 correlation_id（审批中间件与审计中间件同算法，开发文档 §6.7），
  入库单 approval_request_id 关联审批单（选项 B，D1 回归），构成
  "Agent 建议 → 审批事件 → 审计记录"三向闭环（开发文档 §2.5）。
- 规则引擎：加载 A5 交付的 app.agents.review_rules（能力说明 §5，P2-8 整改后
  不再回退 client._rules_fallback 语义分叉镜像）。

运行：脚本经 stdio 客户端自动拉起 server.py（--transport stdio），无需手动起服务；
共建立 6 个角色会话（四路专家 + review-agent + warehouse-manager），各角色复用会话。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client._approval_flow import submit_and_approve
from client.mcp_client import MCPClient

# 双路径锚点（能力说明 §10.1、data_contract §4 种子数据）
PASS_GRN = {"po_no": "PO-20260818001", "grn_no": "GRN-20260820-011",
            "iqc_no": "IQC-20260820-011", "sku": "M-1001", "quantity": 50000,
            "unit_price": 0.0071, "currency": "USD", "supplier_id": "SUP-02",
            "batch_no": "B20260820011"}

FAIL_GRN = {"po_no": "PO-20260818001", "grn_no": "GRN-20260820-012",
            "iqc_no": "IQC-20260820-012", "sku": "M-1001", "quantity": 54000,
            "unit_price": 0.0086, "currency": "USD", "supplier_id": "SUP-01",
            "batch_no": "B20260820012"}

_ROLE_CLIENTS = {
    "qty-check-agent": "qty", "price-check-agent": "price",
    "supplier-check-agent": "supplier", "trace-check-agent": "trace",
}


def _load_review_rules():
    """加载官方规则引擎（app.agents.review_rules，能力说明 §5）。

    P2-8 整改：删除 client._rules_fallback 语义分叉镜像（官方实现已交付，
    空资质 fallback 判 low 而官方判 high 的高风险漏判面消除）；
    官方模块缺失直接抛 ImportError（明确失败，不静默回退分叉实现）。
    """
    from app.agents import review_rules
    return review_rules, "app.agents.review_rules（A5 官方实现）"


async def _rules_review(grn: dict, clients: dict, rules) -> dict:
    """四路证据采集（各专家角色最小可见）+ 确定性规则审查，返回 {evidence, risks, decision}。"""
    po = await clients["qty"].call_tool("get_po", {"po_no": grn["po_no"]})
    quote = await clients["price"].call_tool("get_supplier_quote", {
        "supplier_id": grn["supplier_id"], "sku": grn["sku"]})
    profile = await clients["supplier"].call_tool("get_supplier_profile", {
        "supplier_id": grn["supplier_id"]})
    trace = await clients["trace"].call_tool("get_trace_batch", {"batch_no": grn["batch_no"]})
    risks = {
        "数量": rules.qty_risk(grn["quantity"], po["quantity"])[0],
        "价格": rules.price_risk(grn["unit_price"], quote["p90_min"], quote["p90_max"],
                                 grn["currency"])[0],
        "供应商": rules.supplier_risk(profile)[0],
        "追溯": rules.trace_risk(trace)[0],
    }
    return {"evidence": {"po": po, "quote": quote, "profile": profile, "trace": trace},
            "risks": risks, "decision": rules.compliance_decision(risks)}


def _print_verdict(label: str, verdict: dict) -> None:
    """打印四路证据与合规决策。"""
    print(f"\n  [{label}] 四路证据:")
    for name, data in verdict["evidence"].items():
        print(f"    - {name}: {json.dumps(data, ensure_ascii=False)}")
    print(f"  [{label}] 风险结论: {json.dumps(verdict['risks'], ensure_ascii=False)}")
    print(f"  [{label}] 合规决策: {verdict['decision']}")


def _print_approval_chain(flow: dict) -> None:
    """打印审批事件、审批决策与过账/审计留痕（correlation_id 全链路）。"""
    event = flow["approval_event"]
    print("\n  [审批事件] 审批单已创建（ApprovalMiddleware 唯一入口，D1）:")
    print(f"    - request_id: {event['id']}")
    print(f"    - correlation_id: {event['correlation_id']}（贯穿 Agent 建议 → 审批 → 审计）")
    print(f"    - proposer: {event['proposer']} | amount_cny: {event['amount_cny']}"
          f" | double_review: {event['double_review']}")
    print(f"    - created_at: {event['created_at']}")
    print(f"  [审批决策] approve_inbound_request（warehouse-manager 身份）: "
          f"{json.dumps(flow['decision'], ensure_ascii=False)}")
    print("  [过账/审计留痕] create_inbound_record 执行结果:")
    inbound = flow["inbound"]
    print(f"    - {json.dumps(inbound, ensure_ascii=False)}")
    print("    - 审计说明: 入库单 status=approved、reviewed_by=approval-flow，"
          "approval_request_id 关联审批单；")
    print("      同一 correlation_id 已写入审计事件表（值哈希脱敏，D14），"
          "形成 Agent 建议 → 审批事件 → 审计记录三向闭环（开发文档 §2.5）")


async def main() -> None:
    rules, rules_source = _load_review_rules()
    print("=" * 74)
    print("MIIRP 规则版入库审查演示（demo_inbound_review_rules.py）")
    print("=" * 74)
    print(f"规则引擎: {rules_source}")

    async with (MCPClient("qty-check-agent") as qty_c,
                MCPClient("price-check-agent") as price_c,
                MCPClient("supplier-check-agent") as sup_c,
                MCPClient("trace-check-agent") as trace_c,
                MCPClient("review-agent") as review_c,
                MCPClient("warehouse-manager") as wh_c):
        clients = {"qty": qty_c, "price": price_c, "supplier": sup_c, "trace": trace_c}

        print("\n== 一、工具最小可见（roles 白名单裁剪，api_contract §4 双重拦截）==")
        for role in ("qty-check-agent", "review-agent", "warehouse-manager"):
            client = clients.get(_ROLE_CLIENTS.get(role, role), qty_c)
            if role == "review-agent":
                client = review_c
            elif role == "warehouse-manager":
                client = wh_c
            names = sorted(t.name for t in await client.list_tools())
            print(f"  {role}: {names}")

        print("\n== 二、路径一：通过（PASS / GRN-20260820-011）==")
        verdict = await _rules_review(PASS_GRN, clients, rules)
        _print_verdict("PASS", verdict)
        if verdict["decision"] != "approve":
            raise RuntimeError(f"PASS 路径未按预期得到 approve，实得 {verdict['decision']}")
        flow = await submit_and_approve(PASS_GRN, review_c, wh_c)
        _print_approval_chain(flow)

        print("\n== 三、路径二：拒绝（FAIL / GRN-20260820-012）==")
        verdict2 = await _rules_review(FAIL_GRN, clients, rules)
        _print_verdict("FAIL", verdict2)
        if verdict2["decision"] != "reject":
            raise RuntimeError(f"FAIL 路径未按预期得到 reject，实得 {verdict2['decision']}")
        print("\n  合规复核拒绝创建入库单（符合预期，跳过审批与过账）")

        print("\n== 结论：PASS 路径通过并过账、FAIL 路径被拒绝，双路径均符合预期 ==")


if __name__ == "__main__":
    asyncio.run(main())
