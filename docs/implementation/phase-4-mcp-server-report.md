# AgentDeck Phase 4（MCP 协同服务与工具集）最终实施与验收报告

## 一、验收结论

AgentDeck **Phase 4：MCP 协同服务与工具集** 已完整实施并通过全链路自动化测试与生产级构建验证。

- **官方 SDK 基线**：正式引入官方 MCP Python SDK v2 (`mcp==2.3.0` 与 `mcp-types==2.3.0`)，于 `backend/mcp/` 建立高内聚架构；
- **协议基准合规**：以 **2026-07-28** 无状态官方主规范为核心，独立维护 **2025-11-25 / 2025-03-26** 旧客户端兼容层，按版本严格分流，严禁混用；
- **安全红线闭环**：Stdio 进程严禁 `--token` 明文命令行参数（违规退出码 2），仅支持环境变量 `AGENTDECK_MCP_TOKEN`；HTTP Streamable 端点全面实现非法 Origin 拦截 (403)、256 KB 体积限制 (413)、滑动窗口限流 (429) 以及同事务 Token 原子轮换；
- **同事务写操作幂等**：覆盖 5 类并发与异常场景（串行重放、同 Key 异参冲突、双线程 Barrier 并发唯一性、失败整单回滚、断线重放），业务写入、审计事件与快照落盘处于单一 `SqlAlchemyUnitOfWork` 事务；
- **8 大工具与业务不变量**：完成 `project_context_get`、`task_get`（多会话原子认领防抢占）、`task_revision_get`、`task_report_progress`（自报状态与平台验收状态分离）、`step_update`（修订号冲突拦截）、`checkpoint_request`（`risk_gate` 与高风险强制 `manual_only`）、`decision_get`、`artifact_register`（拦截 Windows 盘符、UNC 与目录穿越）；
- **测试与诊断绿灯**：全量测试套件（Phase 1 至 Phase 4 共 14 个测试）**100% 通过**，前端生产构建零错误，`python run.py --doctor` 报告 21 张规范表与核心组件全部健康。

---

## 二、架构设计与核心组件实现

```
backend/mcp/
├── __init__.py          # 模块导出与版本定义
├── auth.py              # Origin 校验、滑动窗口限流 (429)、Token 认证与 6 大 Scope 映射
├── schemas.py           # 8 大工具 Pydantic v2 Schema、统一错误码映射与稳定响应信封
├── tools.py             # 8 大工具元数据定义与官方 mcp_types.Tool 模型转换
├── service.py           # McpCollaborationService：8 工具领域编排、状态解耦与安全守卫
├── idempotency.py       # McpIdempotencyService：基于 command_idempotency 的同事务幂等执行器
├── server.py            # Stateless2026ProtocolAdapter / Legacy2025CompatProtocolAdapter / Dispatcher
└── stdio_server.py      # python -m backend.mcp.stdio_server：无日志污染的纯净 Stdio 进程
```

### 1. 协议版本分流（落实阻塞项 1）
- **2026-07-28 无状态主规范 (`Stateless2026ProtocolAdapter`)**：
  - 核心为无状态请求模型，每个请求通过 `params._meta.protocolVersion` 或 HTTP 请求头自描述；
  - 移除 `initialize` 握手与 `notifications/initialized`，若收到调用直接返回 JSON-RPC `-32601` 明确错误；
  - 校验 HTTP `Mcp-Method` 与 `Mcp-Name` 请求头，与 JSON-RPC 消息体不一致时立即拦截 (`-32600`)；
  - 移除旧版 `GET /api/v1/mcp` 长连接流（新规范下返回 `HTTP 405 Method Not Allowed`）；
  - 支持 `server/discover`、`tools/list`、`tools/call` 与 `ping`。
- **2025-11-25 / 2025-03-26 兼容规范 (`Legacy2025CompatProtocolAdapter`)**：
  - 为旧客户端保留独立的传统处理器，支持 `initialize` 握手与 `notifications/initialized`；
  - 显式携带 `MCP-Protocol-Version: 2025-11-25` 时，`GET /api/v1/mcp` 兼容返回 SSE 事件流。
- **能力矩阵解耦**：
  - 将 MCP 能力键统一为 `mcp_client_collab`，与 `L1` 的 `runtime_registry_and_status` 明确解耦；
  - `CapabilityManifestItem` 增加 `verified_protocol_versions: list[str]`，仅当具备真实实测证据时方可标为 `supported`。

### 2. Stdio 凭证安全隔离（落实阻塞项 3）
- 启动入口 `python -m backend.mcp.stdio_server` 在参数解析最前置阶段扫描 `sys.argv`；
- 检测到任何 `--token`、`-t`、`agmcp_` 等参数时，**严禁启动服务，立即向 `stderr` 打印安全违规说明并以退出码 `2` 终止**，确保进程列表中绝不泄露凭证；
- 凭证仅通过环境变量 `AGENTDECK_MCP_TOKEN` 传递；
- 严格隔离 `stdout` 与 `stderr`：`stdout` 仅输出协议级 JSON-RPC 响应，所有日志、诊断和报错统一走 `stderr` 与 `logs/error.log`。

### 3. HTTP Streamable 安全加固（落实修正项 4）
- **Origin 校验**：对携带外部/恶意 `Origin` 的跨域请求直接拦截，返回 `HTTP 403 Forbidden`；
- **体积限制**：限制请求体最大 `256 KB`，超限请求立即返回 `HTTP 413 Payload Too Large`；
- **滑动窗口限流**：针对连续认证失败（1 分钟内超 10 次）自动封禁客户端 IP，返回 `HTTP 429 Too Many Requests` 并写入 `security.mcp_auth_rate_limited` 审计事件；
- **Token 原子轮换**：支持 `POST /api/v1/projects/{project_id}/mcp-tokens/{token_id}/rotate`，在同一数据库事务中吊销旧 Token 并签发继承原权限的新 Token。

### 4. 同事务写操作幂等（落实阻塞项 5）
- `McpIdempotencyService` 强制要求在单一 `SqlAlchemyUnitOfWork` 中完成：
  1. `command_idempotency` 插入占位行并执行 `uow.flush()`，利用主键唯一约束与写锁拦截并发重复；
  2. 执行核心领域逻辑（更新 Task、Step、Decision 或登记 Artifact）；
  3. 写入 `events` 表审计事件；
  4. 将序列化后的响应保存至 `response_snapshot_json`；
  5. 事务原子 `commit()`。
- **5 类场景实测全通过**：
  - 场景 1（串行重放）：相同 Key + 相同参数再次请求，直接重放首次快照，不重复落库；
  - 场景 2（冲突拦截）：相同 Key + 篡改参数，返回 `MCP_IDEMPOTENCY_CONFLICT`；
  - 场景 3（高并发）：双线程利用 `threading.Barrier(2)` 严格对齐并发请求，数据库只产生 1 条业务记录；
  - 场景 4（失败回滚）：业务逻辑失败时事务整体回滚，`command_idempotency` 不残留脏快照；
  - 场景 5（断线重试）：模拟子进程中断重启后，凭原 Key 成功恢复首次执行结果。

### 5. 8 大工具不变量与安全守卫
- `project_context_get`：自动脱敏，不输出 Token 哈希等机密字段；
- `task_get`：支持 `claim_next_ready` 与显式认领，若任务已被其他活跃 Session 抢占，原子拦截并返回 `MCP_VERSION_CONFLICT`；
- `task_revision_get`：只读读取历史快照与当前生效快照；
- `task_report_progress`：Agent 自报 `completed` 仅修改 `agent_reported_status`，绝不修改 `platform_status`；
- `step_update`：强制比对 `expected_revision_no`，用户修订上游后 Agent 尝试提交立即拦截 (`MCP_VERSION_CONFLICT`)；
- `checkpoint_request`：`decision_type == risk_gate`（无论风险高低）以及 `high/critical` 风险强制 `manual_only`，严禁设置 `auto_submit_recommended`；
- `decision_get`：支持轮询获取人工拍板结果；
- `artifact_register`：拦截 Windows 盘符 (`C:\`)、UNC (`\\server\share`)、反斜杠、`..` 目录穿越及符号链接逃逸，限制 `summary_content <= 64 KB`、`metadata <= 16 KB`。

---

## 三、真实 Agent 兼容性验证

结合本机已安装环境验证并登记能力记录：

| Runtime | 实际安装环境与路径 | 协议通道 | 验证通过的协议版本 | 状态声明 |
| :--- | :--- | :--- | :--- | :--- |
| **Claude Code** | `C:\Users\xhma10\.local\bin\claude.exe` (v2.1.114) | Stdio (`AGENTDECK_MCP_TOKEN`) / HTTP Streamable | `2026-07-28`, `2025-11-25` | `supported` |
| **Codex CLI** | `C:\Users\xhma10\AppData\Roaming\npm\codex.ps1` (v0.104.0) | Stdio / JSON-RPC | `2025-11-25`, `2025-03-26` | `supported` |
| **OpenCode** | `C:\Users\xhma10\AppData\Roaming\npm\opencode.ps1` (v1.1.51) | HTTP Streamable (`POST /api/v1/mcp`) | `2026-07-28`, `2025-11-25` | `supported` |
| **Antigravity (AGY)** | `C:\Users\xhma10\AppData\Local\agy\bin\agy.EXE` (v1.19.6) | Stdio Subprocess | `2026-07-28` | `supported` |

---

## 四、自动化回归验证记录

### 1. 后端全量测试套件
```powershell
python -m pytest -q
..............                                                           [100%]
14 passed, 2 warnings in 24.48s
```
- `tests/test_phase1_clean_slate_domain.py`：3 passed
- `tests/test_phase2_rest_api.py`：1 passed
- `tests/test_phase3_remote_registry.py`：5 passed（含 Playwright 真实浏览器 E2E 测试）
- `tests/test_phase4_mcp_server.py`：5 passed

### 2. 前端构建与 UI 验证
```powershell
cd frontend; npm run build
✓ 1580 modules transformed.
dist/index.html                   0.82 kB │ gzip:  0.47 kB
dist/assets/index-BPBrCDg0.css    6.23 kB │ gzip:  1.86 kB
dist/assets/index-CCmx6Bq7.js   222.39 kB │ gzip: 65.59 kB
✓ built in 3.38s
```
- P06 项目管理面板：支持一键签发、同事务轮换与吊销 MCP Token；展示标准 Stdio 环境变量接入配置示例（不含 `--token`）与 HTTP 端点接入说明；
- P10 / P14 会话与注册中心：展示各 Runtime 验证通过的 MCP 协议版本清单 (`verified_protocol_versions`)。

### 3. 主系统体检 (`python run.py --doctor`)
```text
════════════════════════════════════════════════════════════════════════
  🩺 AgentDeck v1.3.0 环境诊断体检报告
════════════════════════════════════════════════════════════════════════
  [✓] 数据库路径: D:\Development Files\PythonProjects\stu\AgentDeck\data\agentdeck.db
  [✓] SQLite PRAGMA: {'journal_mode': 'wal', 'foreign_keys': 1, 'busy_timeout': 15000}
  [✓] 规范表数量: 21 张
  [✓] RuntimeProviders: ['antigravity', 'claude_code', 'codex', 'opencode']
  [✓] Machines: ['Local Host (XHMA10-T14)']
  [✓] VAPID 公钥长度: 87
  [i] 监听端口: 8765
════════════════════════════════════════════════════════════════════════
```

---

## 五、下一步建议（进入 Phase 5：Web Push 与桌面通知中心）

Phase 4 的完工标志着外部 Agent 具备了与 AgentDeck 双向协同的标准通道（主动认领任务、提交修订进度、触发决策阻断、登记交付物）。
下一阶段 **Phase 5** 的核心目标是：
1. **多端 Web Push 实时唤醒**：当 Agent 发起 `checkpoint_request` 或出现任务阻断时，通过 VAPID 协议推送原生通知至已配对设备（手机/浏览器）；
2. **通知防抖与合并**：针对高频日志汇报实现通知限频合并，优先保证风险门禁与待决策卡片的即时到达；
3. **P13 移动端响应优化**：完善移动端扫码信任与单手快速拍板交互。

