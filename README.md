# MIIRP · 物料入库智能审查平台

基于 MCP（Model Context Protocol）的多 Agent 物料入库智能审查平台。平台以 MCP Server 形态提供采购订单（PO）、收货单（GRN）、质检（IQC）、库存、供应商、追溯、入库审批等业务域共 **9 个工具文件 / 16 个 MCP 工具**，并通过「四路专家 Agent 并行审查 + 合规复核」的多 Agent 编排，对入库业务进行智能风险审查与审批管控。

## 核心特性

- **多 Agent 智能审查**：数量 / 价格 / 供应商 / 追溯四路专家 Agent 并行采集证据并给出风险结论，合规复核 Agent 按内控规则汇总输出 `approve | review | reject`；LLM 输出解析失败自动兜底高风险（防漏放行）。
- **双模式运行**：默认 `mock` 模式由规则引擎判定（可离线演示、评测可复现），可切换 `openai-compatible` 模式接入真实大模型。
- **写操作审批流**：写工具（如 `create_inbound_record`）自动进入人工审批流程，高价值单据（≥ ¥100,000）双人审批且异人校验；审批通过后才执行业务落库。
- **纵深安全防线**：工具层 RBAC 白名单（最小可见）+ Agent 侧 `ToolGuard` 参数护栏，双重拦截越权调用与 Prompt 注入；全链路审计留痕与 `correlation_id` 关联。
- **业务一致性保障**：入库过账前强制校验 GRN/IQC/PO 引用完整性、IQC 合格、单据间字段一致性；以审批单号为幂等键，重复执行不产生重复单据。
- **工程化完备**：分层配置（yaml + 环境变量）、JSON 结构化日志（含 trace_id）、限流、指标端点、健康检查、CI 质量门禁（覆盖率 ≥ 80%）、备份 / 恢复 / 归档脚本。

## 技术栈

| 层 | 技术 |
|---|---|
| 运行时 | Python 3.12 |
| MCP 框架 | FastMCP 3.4.7 |
| HTTP | FastAPI + Uvicorn |
| 数据校验 | Pydantic |
| 数据库 | SQLite（本地默认）/ MySQL 8（生产默认）/ PostgreSQL（备选） |
| 测试与质量 | pytest、pytest-asyncio、pytest-cov、ruff |
| 部署 | Docker / docker-compose / Nginx（HTTPS） |
| CI | GitHub Actions（lint + test + eval + trivy 安全扫描 + 镜像构建） |

## 快速开始

### 1. 环境准备

```bash
# 创建 conda 环境（Python 3.12，依赖版本锁定，含清华镜像配置）
conda env create -f env.yml

# 或使用 pip（以 requirements.txt 为依赖事实源）
pip install -r requirements.txt
```

### 2. 启动服务

```bash
# 开发模式（stdio 传输，默认 SQLite）
python server.py --transport stdio

# 生产模式（streamable-http 传输，监听 0.0.0.0:8000）
python server.py --transport streamable-http
```

Windows / Linux 也可直接使用启动脚本：`.\scripts\start.ps1` / `./scripts/start.sh`。

### 3. 访问 HTTP 端点

streamable-http 模式启动后提供：

| 端点 | 说明 |
|---|---|
| `/mcp` | MCP 协议端点（streamable-http 传输） |
| `/healthz` | 健康检查（含数据库状态） |
| `/metrics` | Prometheus 指标（生产环境需 `X-MIIRP-Key` 鉴权） |
| `/approvals` | 审批待办查询（审批角色可访问） |

前端控制台：直接打开 `frontend/index.html` 即可体验可视化控制台。

## 配置说明

配置采用分层合并：`config/default.yaml` → `config/{env}.yaml` → `MIIRP_` 前缀环境变量（最高优先级）。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `MIIRP_ENV` | 运行环境（default / production） | `default` |
| `MIIRP_DB_DSN` | 数据库连接串（生产必填，缺失即 fail-fast） | `sqlite:///data/miirp.db` |
| `MIIRP_LLM_API_KEY` | LLM API Key（openai-compatible 模式必填） | 空 |
| `MIIRP_DB_PASSWORD` | 生产 MySQL 密码（部署编排用） | 空 |

关键配置项（`config/default.yaml`）：

- `roles`：角色 → 工具白名单（如 `warehouse-manager`、`compliance-officer`、`iqc-inspector` 等 11 个角色）。
- `approval`：审批策略（`write_tools`、`high_value_threshold_cny`、`timeout_seconds`、`approve_roles`）。
- `agents.llm_mode`：`mock`（默认，规则引擎判定）或 `openai-compatible`（接入大模型）。
- `rate_limit`：每 client_id 每秒请求上限（默认 20 QPS，滑动窗口）。
- `metrics.auth`：`/metrics` 端点鉴权开关（生产默认开启）。

## 项目结构

```
MaterialInbound/
├── server.py                    # MCP Server 入口（中间件装配 + 工具注册 + HTTP 路由）
├── app/
│   ├── agents/                  # Agent 编排层（LLM 客户端、审查 Agent、ToolGuard 护栏、提示词、规则）
│   ├── approval/                # 审批服务（审批单创建、决策、双人审批）
│   ├── audit/                   # 审计留痕存储（TTL 保留策略）
│   ├── auth/                    # 密钥 → 角色解析注册
│   ├── db/                      # 数据库连接与幂等迁移
│   ├── middleware/              # 治理中间件链：AuthRole → RateLimit → Audit → Approval → ErrorHandling
│   ├── services/                # 业务服务（PO/GRN/IQC/库存/入库/供应商/追溯/通知/汇率/规则/种子）
│   └── tools/                   # 9 个 MCP 工具模块（16 个工具）
├── client/                      # MCP 客户端与演示（Agent 版 / 规则版入库审查）
├── config/                      # 分层配置（default.yaml / production.yaml）
├── frontend/                    # 控制台前端
├── migrations/                  # 数据库迁移脚本（MySQL / PostgreSQL / SQLite）
├── scripts/                     # 运维脚本（启动 / 备份 / 恢复 / 归档 / 评测 / 证书生成）
├── tests/                       # unit / integration / e2e / eval 四层测试
├── docs/                        # 工程文档中心（契约 / 审查 / 进度看板）
├── MIIRP_deployment/            # 部署编排（Dockerfile / docker-compose / nginx / 证书）
└── .github/workflows/ci.yml     # CI 质量流水线
```

## 多 Agent 审查流程

```
入库数据 ──► 四路专家 Agent（并行）         合规复核 Agent
              ├─ qty-check-agent  (get_po)       │
              ├─ price-check-agent(报价区间)      ├─ approve ──► 高价值？──► 双人审批 ──► 落库 + 库存联动
              ├─ supplier-check-agent(档案)      ├─ review  ──► 转人工审批
              └─ trace-check-agent (批次追溯)     └─ reject  ──► 拒绝
```

- 专家 Agent 与合规复核均经过 `ToolGuard` 白名单 + 参数形态校验，防止注入越权。
- 审批通过的写操作以 `correlation_id` 关联证据、决策与审计记录。
- 入库过账前强制校验 GRN/IQC/PO 引用完整性与一致性，IQC 未通过禁止过账。

## 测试与质量门禁

```bash
# 代码风格检查
ruff check app/ client/ tests/ scripts/

# 单元 / 集成 / E2E 测试（覆盖率 < 80% 即失败）
pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=80

# Agent 评测门禁（accuracy=1.0 且漏拒率=0 方可通过）
python scripts/run_evals.py
```

当前状态：**201 项测试全部通过，覆盖率 94.91%**；评测 accuracy=1.0、漏拒率=0；M0–M6 全部里程碑门禁通过，终审无 P0/P1。

## 部署（Docker）

```bash
cd MIIRP_deployment
# 准备环境变量（参考 .env.example，设置 MIIRP_DB_PASSWORD 等）
# 本地构建镜像并编排启动（mcp-server + mysql + nginx HTTPS）
docker compose up -d --build
```

生产环境注意事项：

- 必须注入 `MIIRP_ENV=production` 与 `MIIRP_DB_DSN`（缺失时应用启动即退出，禁止静默回退 SQLite）。
- 默认使用 MySQL 8（PostgreSQL 备选），Nginx 网关终止 TLS（证书位于 `MIIRP_deployment/certs/`）。
- CI 流水线含 trivy 安全扫描（HIGH/CRITICAL 阻断）与镜像可构建性验证。

## 文档导航

| 文档 | 说明 |
|---|---|
| `docs/README.md` | 工程文档索引（契约 / 审查 / 进度三层组织） |
| `docs/progress.md` | 里程碑与任务卡状态看板 |
| `docs/contracts/api_contract.md` | MCP 工具签名、HTTP 路由、角色白名单矩阵、审批拦截契约 |
| `docs/contracts/data_contract.md` | 数据模型、种子数据、TTL 保留策略 |
| `docs/contracts/module_boundaries.md` | 模块边界与所有权红线 |
| `docs/contracts/defect_regression.md` | 缺陷（D1–D32）→ 实现/测试回归基线 |

## 许可证与说明

本项目为内部工程化教学与演示项目（多 Agent 协作开发规范实践）。涉及生产使用请自行评估安全合规要求，并按需补充 OAuth 认证、证书管理（`scripts/gen-cert.ps1` 可生成自签名证书）与密钥托管方案。
