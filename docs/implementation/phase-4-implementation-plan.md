# AgentDeck Phase 4（MCP 协同服务与工具集）文件级实施计划（修订定稿版）

> **阶段定位**：Phase 4 — Agent → AgentDeck 标准 MCP 协同服务、最小权限 Token 守卫、8 大核心协作工具与端到端实测  
> **架构基线**：`04_AgentDeck_Architecture_and_Roadmap_v1.3.md`、`03_Phased_Development_Implementation_Plan_v1.0.md` 及 Phase 4 审阅修订指令  
> **分支约束**：在当前 `feat/project-task-foundation` 分支增量开发，严禁修改 `main` 分支，严禁覆盖未提交工作区修改。

---

## 一、审阅修正要点与前置概念统一

### 1.1 明确区分 L1 控制等级与 MCP 协作协议
- **`L0`–`L3`（控制能力等级）**：描述 **AgentDeck → Runtime** 的控制深度：
  - `L0`：官方 Remote 入口（`remote_entry`）
  - `L1`：Runtime 注册、状态、能力清单管理（`runtime_registry_and_status`）
  - `L2`：程序化控制（`programmatic_control`）
  - `L3`：审批与恢复能力（`approval_and_resume`）
- **MCP（协作协议与工具接口）**：描述 **Agent → AgentDeck** 的主动协作通道（能力键统一命名为 `mcp_client_collab`）：
  - 与 `L1` 解耦，不作为 `L1` 的判断条件；
  - 在 Capability Manifest 中记录每个 Runtime 实际验证通过的 MCP 协议版本（如 `verified_protocol_versions`），未经实测前保持 `unverified`。

### 1.2 Phase 4 产品职责边界（严禁把 MCP 变成 Agent 执行引擎）

| AgentDeck 负责（通过 MCP 暴露） | Agent 自身负责（MCP 绝不越界） |
|---|---|
| 项目与任务上下文按需读取（`project_context_get` / `task_get` / `task_revision_get`） | 模型推理、Prompt 组装与上下文窗口管理 |
| 任务进度汇报与步骤状态回写（`task_report_progress` / `step_update`） | Agent Loop、规划、子 Agent 调度 |
| 发起人工决策门禁与查询决策结果（`checkpoint_request` / `decision_get`） | 本地文件编辑、终端命令执行、工具调用 |
| 项目内交付物归档与校验（`artifact_register`） | Runtime 原生工具权限审批（如写文件/跑命令确认） |
| 全链路审计事件记录（`events` 表与 P10 时间线展示） | Runtime 内部会话生命周期控制 |

---

## 二、协议版本策略与官方 MCP Python SDK v2 架构（4.1）

### 2.1 协议基准与多版本分层兼容策略（修正阻塞项 1）
1. **目标主规范：`2026-07-28`（无状态请求模型）**
   - 取消传统有状态 `initialize` 握手、`notifications/initialized` 通知、协议级 Session ID 及旧版 HTTP GET Stream；
   - 每个请求通过 `_meta`（如 `_meta.protocolVersion`）自描述协议版本与上下文；
   - 支持新版无状态 POST 请求路由，解析并校验 `Mcp-Method`、`Mcp-Name` 请求头（当调用 `tools/call` 时校验 `Mcp-Name` 与工具名一致）；
   - 支持新版可选服务发现 `server/discover` 以及无状态 `tools/list`、`tools/call`、`ping`。
2. **旧客户端兼容层：`2025-11-25` / `2025-03-26`**
   - 针对尚未升级到 `2026-07-28` 的宿主 Agent 客户端，通过**独立的版本分发器（Versioned Protocol Dispatcher）**提供 `2025-11-25` / `2025-03-26` 兼容支持（含 `initialize`、`notifications/initialized` 及 Streamable HTTP `POST / GET`）；
   - **严禁在同一处理器内混用两套协议行为**：网关入口先根据请求头（`Mcp-Method` / `MCP-Protocol-Version`）与请求体（`_meta.protocolVersion` 或 `method == "initialize"`）识别协议世代，再分发至 `Stateless2026Adapter` 或 `Legacy2025CompatAdapter`。

### 2.2 基于官方 MCP Python SDK v2 (`mcp>=2.0.0`) 的模块架构（修正项 2）
引入官方 `mcp>=2.0.0`（当前最新稳定版 `2.3.0`）承担底层 MCP Server/Tools/Schema 定义与 Stdio/HTTP 协议编解码，AgentDeck 专注领域编排与安全边界：

```text
backend/
├── mcp/
│   ├── __init__.py       # 模块导出
│   ├── server.py         # 官方 MCP Python SDK v2 (MCPServer) 实例与 2026-07-28 / 2025-11-25 协议分发器
│   ├── tools.py          # 8 个 MCP 工具注册与官方 SDK Tool 绑定
│   ├── schemas.py        # 8 个工具的 Pydantic v2 入参/出参模型与统一错误结构
│   ├── service.py        # AgentDeck 业务编排（连接 Project/Planning/Decision/Artifact/Session 服务）
│   ├── auth.py           # Project Token 鉴权、细粒度 Scope、Origin 校验、认证限流与请求体积守卫
│   ├── idempotency.py    # 同事务原子幂等服务（基于 command_idempotency 表）
│   └── stdio_server.py   # Stdio 启动入口（仅支持 AGENTDECK_MCP_TOKEN 环境变量）
```

---

## 三、Stdio 与 HTTP Streamable 安全规范（4.1 / 4.2）

### 3.1 Stdio 传输安全规范（修正阻塞项 3）
1. **严禁命令行明文 `--token` 参数**：
   - `python -m backend.mcp.stdio_server` **仅从环境变量 `AGENTDECK_MCP_TOKEN` 读取凭证**；
   - 若检测到命令行参数中包含 `--token` 或任何类似明文密钥参数，立即向 `stderr` 输出安全告警并以非零状态码退出，防止 Token 泄露到 Windows 进程命令行列表、任务管理器或审计日志。
2. **Stdout / Stderr 严格隔离**：
   - `stdout` **仅输出合法的 MCP JSON-RPC 协议消息**（UTF-8 单行 JSON）；
   - 所有启动提示、结构化错误日志、SQLAlchemy/Alembic 日志全部重定向至 `stderr` 与 `logs/error.log`。
3. **标准客户端配置示例**：
   ```json
   {
     "mcpServers": {
       "agentdeck": {
         "command": "python",
         "args": ["-m", "backend.mcp.stdio_server"],
         "env": {
           "AGENTDECK_MCP_TOKEN": "<PROJECT_TOKEN>"
         }
       }
     }
   }
   ```

### 3.2 HTTP 传输安全规范（修正项 4）
1. **本机默认绑定与零信任鉴权**：
   - 服务默认监听 `127.0.0.1`（不默认暴露 `0.0.0.0`）；
   - `/api/v1/mcp` 端点严禁复用 Web 管理端的 `127.0.0.1` 免密规则，所有请求必须携带有效 `Authorization: Bearer agmcp_...` 或 `X-MCP-Project-Token: agmcp_...`。
2. **Origin 校验（防 DNS Rebinding 与跨站调用）**：
   - 若请求携带 `Origin` 头，强制校验其主机名仅允许本机白名单（`127.0.0.1`、`localhost`、`[::1]`）或已配对受信任来源；不合法的 `Origin` 直接返回 **`HTTP 403 Forbidden`**。
3. **新版 `2026-07-28` 与旧版 `2025-11-25` HTTP 行为分离**：
   - 新版 `2026-07-28`（`POST /api/v1/mcp`）：校验 `Mcp-Method` 请求头与 JSON-RPC `method` 一致；当 `method == "tools/call"` 时校验 `Mcp-Name` 请求头与 `params.name` 一致；处理 `_meta` 版本信息；无状态响应，不依赖 Session ID；支持 `server/discover`。
   - 旧版 `2025-11-25` / `2025-03-26` 兼容（`POST /api/v1/mcp` & `GET /api/v1/mcp`）：支持旧版 `initialize` 协商；对旧版 `GET /api/v1/mcp` 校验認證与 `Origin` 后按规范返回 SSE 端点或 `405`（当处于纯 `2026-07-28` 模式时明确拒绝旧版 GET Stream）。
   - `GET /api/v1/mcp/tools` 仅作为 AgentDeck 管理台自身的诊断 REST 接口保留，不作为 MCP 协议的 `tools/list` 替代品。
4. **请求防护与限流**：
   - **请求体大小限制**：单次 HTTP MCP 请求体上限 `256 KB`（超限返回 `HTTP 413 Payload Too Large`）。
   - **超时与并发控制**：单次工具调用超时上限 `15s`，进程内设置异步并发信号量（默认 `32`）。
   - **认证失败限流**：按来源 IP 维护滑动窗口，连续认证失败超阈值（如 60 秒内 15 次）返回 `HTTP 429 Too Many Requests` 并记录 `security.mcp_auth_rate_limited` 事件。

---

## 四、最小权限矩阵与 Token 轮换（4.2）

| Scope 标识 | 含义 | 允许调用的 MCP 工具 |
|---|---|---|
| `context:read` | 读取项目工作上下文摘要 | `project_context_get` |
| `task:read` | 读取任务、步骤、依赖与修订快照 | `task_get`（只读）、`task_revision_get` |
| `progress:write` | 认领任务、汇报进度、更新步骤状态 | `task_get`（含 `claim_for_session_id`）、`task_report_progress`、`step_update` |
| `decision:write` | 发起人工决策/检查点请求 | `checkpoint_request` |
| `decision:read` | 查询人工决策处理结果 | `decision_get` |
| `artifact:write` | 登记项目内结构化交付物 | `artifact_register` |

- **服务端强制解析 `project_id`**：从 Token 唯一解析 `principal.project_id`，绝不信任 Agent 传入的 `project_id`；任何跨项目 `project_id` / `task_id` / `step_id` / `decision_id` / `session_id` 均经由 `enforce_project_scope` 拦截，返回 `MCP_CROSS_PROJECT_DENIED`。
- **Token 原子轮换**：新增 `POST /api/v1/projects/{project_id}/mcp-tokens/{token_id}/rotate`，在同一数据库事务内吊销旧 Token 并签发同 Scope 的新 Token。

---

## 五、写操作幂等的数据库事务级原子性（修正阻塞项 5）

所有写工具（`task_get[claim]`、`task_report_progress`、`step_update`、`checkpoint_request`、`artifact_register`）必须传入非空 `idempotency_key`，并在 **同一个 `SqlAlchemyUnitOfWork` 数据库事务** 中完成以下 7 步：

```text
BEGIN IMMEDIATE TRANSACTION (SqlAlchemyUnitOfWork)
  1. 计算复合幂等键 scoped_key = "mcp:{project_id}:{tool_name}:{idempotency_key}" 与规范化参数指纹 payload_hash (SHA-256)
  2. 查询 command_idempotency 表是否已存在 scoped_key：
     - 若已存在且记录内 _payload_hash == payload_hash：直接返回已保存的首次响应快照（标记 is_idempotent_replay = true），不执行任何写操作
     - 若已存在但 _payload_hash != payload_hash：立即抛出 MCP_IDEMPOTENCY_CONFLICT
  3. 先向当前事务插入（flush）command_idempotency 占位行（利用主键 PK 唯一约束 + SQLite 写锁防止双线程并发穿透；若并发触发 IntegrityError，立即回滚当前事务并重读已提交的首次快照或冲突结果）
  4. 执行领域业务变更（Task / Step / Decision / Artifact 更新）
  5. 调用 uow.record_event(...) 写入业务审计事件与 mcp.tool_called 事件
  6. 将生成的业务响应 JSON 与 _payload_hash 写入该 command_idempotency 行的 response_snapshot_json (status = 'done')
COMMIT TRANSACTION
```

- **异常回滚保证**：若步骤 4–6 任意环节抛出业务异常（如版本冲突、路径非法、外键/状态不合法），整个事务原子回滚，`command_idempotency` 表**绝不残留任何幂等记录**，客户端修复参数后可正常重试。
- **断线重放保证**：若服务端事务已 `COMMIT` 成功，但网络中断导致客户端未收到响应，客户端使用同一 `idempotency_key` 重试时将命中步骤 2，直接收到首次成功响应且不产生重复数据。

---

## 六、8 大核心 MCP 工具契约与补充安全边界（4.3 – 4.5）

1. **`project_context_get`** (`context:read`)：
   - 仅返回绑定项目的摘要、待办/进行中任务列表（上限 50）、未决 Decision 摘要与活跃 Session 摘要。
2. **`task_get`** (`task:read`，认领需 `progress:write`)：
   - **原子认领竞争保护（落实补充项 2）**：当传入 `claim_for_session_id` 时，在同一事务内检查该 Task 是否已被同项目下**另一个处于 `active` / `waiting_interaction` 状态的 Session** 绑定认领；若已被其他活跃 Session 抢占，拒绝并返回 `MCP_STATE_NOT_ALLOWED`（同一 Session 重复认领则幂等放行），并通过条件更新 `UPDATE tasks SET version = version + 1 ... WHERE id = :id AND version = :expected_ver` 保证并发认领安全。
3. **`task_revision_get`** (`task:read`)：
   - 返回指定或最新 `TaskRevision` 快照、`active_revision_no`、`needs_revalidation` 与 `revalidation_reason`。
4. **`task_report_progress`** (`progress:write`)：
   - 仅更新 `task.agent_reported_status` 并记录 `task.progress_reported` 事件；**即使汇报 `completed` 也绝不修改 `task.platform_status`**。
5. **`step_update`** (`progress:write`)：
   - 强制校验 `step.active_revision_no == expected_revision_no`（及可选 `expected_version`）且 `step.needs_revalidation == 0`，否则返回 `MCP_VERSION_CONFLICT`；仅更新 `step.agent_reported_status`，不越权将 `step.platform_status` 置为 `completed`。
6. **`checkpoint_request`** (`decision:write`)：
   - **高风险门禁强制人工确认（落实补充项 1）**：只要 `decision_type == "risk_gate"`（无论 `risk_level` 为何值）或者 `risk_level in ("high", "critical")`，一律强制 `timeout_policy = "manual_only"`（若调用方显式请求 `auto_submit_recommended` 则直接返回 `MCP_STATE_NOT_ALLOWED` 拒绝），绝不允许超时自动通过。
7. **`decision_get`** (`decision:read`)：
   - 校验项目归属后返回 Decision 当前 `status`、`chosen_option_id`、`custom_note`、`resolved_by`、`resolved_at`。
8. **`artifact_register`** (`artifact:write`)：
   - **路径与载荷安全校验（落实补充项 3）**：
     - 校验 `relative_path`：严禁 Windows 盘符（如 `C:\`、`D:/`）、严禁 UNC 路径（`\\server\share` 或 `//server`）、严禁绝对路径与 `..` 父目录跨越；解析 `resolve()` 后必须严格位于 `project.root_path` 之下，拦截符号链接逃逸；
     - 限制 `summary_content` 长度 `<= 65536` 字符，`metadata` JSON 序列化后大小 `<= 16384` 字节且执行敏感凭证脱敏校验。

---

## 七、执行顺序与验证清单（4.1 – 4.6）

| 阶段 | 实施内容 | 关键验收指标 |
|---|---|---|
| **4.1** | 官方 `mcp` SDK v2 集成、`2026-07-28` 主协议与 `2025-11-25` 兼容分发器、Stdio/HTTP 入口 | `2026-07-28`（`Mcp-Method`/`Mcp-Name`/`_meta`/`server/discover`/无状态）与 `2025-11-25`（`initialize`）双模测试通过；Stdio 传入 `--token` 被拒，仅接受 `AGENTDECK_MCP_TOKEN` 且 `stdout` 纯净。 |
| **4.2** | Token 认证、细粒度 Scope、Origin 守卫、限流与 Token 轮换 | 非法 `Origin` 返回 `403`；超限请求体返回 `413`；连续错误 Token 触发 `429`；跨项目访问返回 `MCP_CROSS_PROJECT_DENIED`；Token 轮换原子生效。 |
| **4.3** | `project_context_get`、`task_get`（含并发认领竞争）、`task_revision_get` | 双 Session 并发认领同一 Task 仅一个成功，另一个返回 `MCP_STATE_NOT_ALLOWED`。 |
| **4.4** | `task_report_progress`、`step_update` | Agent 汇报 `completed` 不改变 `platform_status`；用户修订 Step 后旧 `expected_revision_no` 返回 `MCP_VERSION_CONFLICT`。 |
| **4.5** | `checkpoint_request`、`decision_get`、`artifact_register` | `risk_gate`（含 `low/normal` 风险）与 `high/critical` 风险请求自动通过均被拒；Windows 盘符/UNC/`..`/超长 `metadata` 被拒。 |
| **4.6** | 同事务并发幂等测试、断线重放测试、本机真实 Agent CLI 兼容性验证与前端集成 | 覆盖串行重放、同 Key 异参冲突、双线程 `Barrier` 并发幂等、失败回滚不留快照、断线重连恢复；实测本机 Agent CLI / MCP Client 兼容性并在 Capability Manifest 记录真实协议版本。 |
