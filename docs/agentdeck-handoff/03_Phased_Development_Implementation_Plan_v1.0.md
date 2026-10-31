# AgentDeck 分阶段开发实施计划
**版本：1.0**  
**依据：**《AgentDeck 最终架构复核与重构决策书 v1.1》  
**配套：**《AgentDeck 全产品页面原型与交互规格书 v1.0》  
**用途：**交付 Claude Code / Antigravity / Codex 等开发 Agent，按阶段实施、验证和提交  
**开发原则：**先验证关键技术风险，再建立领域底座；先打通一条真实协作链路，再扩展 Runtime；每阶段可独立验收、可回滚。

---

# 0. 执行摘要

## 0.1 最终产品定位

AgentDeck 是 Agent 协作管理平台，不是 Agent Runtime、Agent Harness 或通用工作流引擎。

平台负责：
- Project、Task、Step、Revision；
- Session 的项目归属和可选任务关联；
- Execution、Attempt、状态、历史；
- Decision、人工检查点和审计；
- Artifact 登记和追溯；
- Revision 修改后的下游影响识别；
- MCP 协作接口和 Runtime Connector 管理。

外部 Agent / Runtime 负责模型推理、规划、工具调用、Agent Loop 和内部上下文。平台不得自行实现一套通用 Agent Loop。

## 0.2 总体实施顺序

| 阶段 | 名称 | 核心结果 | 级别 |
|---|---|---|---|
| 0 | 仓库复核与架构冻结 | 确认现状、差异、ADR、风险清单 | 必须 |
| 1 | 技术验证与工程基线 | Windows 环境、SQLite、Worker、MCP smoke test | 必须 |
| 2 | 基础工程与开发体验 | Python/FastAPI、React/TS、迁移、Mock、CI | 必须 |
| 3 | 核心领域模型与数据库 | Project/Task/Step/Revision/Session | 必须 |
| 4 | 核心 REST API 与状态服务 | 项目、任务、步骤、版本、事件查询 API | 必须 |
| 5 | 前端工作台基础与任务树 | 导航、项目、任务树、任务详情基础 | 必须 |
| 6 | AgentDeck MCP Server | 外部 Agent 可读写任务并请求决策 | 必须 |
| 7 | Event / Decision / 恢复机制 | 事件持久化、审批恢复、断线恢复 | 必须 |
| 8 | Execution / Attempt / Worker | 平台托管 Runtime 的统一执行生命周期 | 必须 |
| 9 | 首个托管 Runtime | Claude 或 OpenCode 真实集成 | 按产品优先级 |
| 10 | Artifact / Revision 影响分析 | 产物追溯、下游复核和版本闭环 | 必须 |
| 11 | 完整前端体验 | 收件箱、Session、Artifact、移动端、设置 | 必须 |
| 12 | 第二 Runtime / Codex / ACP | 验证 Connector 可扩展性 | 后续 |
| 13 | 安全加固、发布与运维 | 备份、权限、升级、跨平台发布 | 发布门槛 |

建议不要把“页面全部完成”放在“真实 MCP 链路”之前。前端可用 Mock 并行开发，但第一条真实业务闭环应尽早完成。

## 0.3 关键决策冻结

以下内容开发中不得随意更改；如需更改必须提交 ADR：
1. Backend：Python 3.12+、FastAPI、Pydantic v2、SQLAlchemy 2.x、Alembic。
2. Frontend：TypeScript、React、Vite；数据请求使用 TanStack Query；Zustand 只保存轻量 UI 状态。
3. Database：SQLite WAL，外键开启；不引入 PostgreSQL、Redis、Kafka、Celery、微服务。
4. 架构：模块化单体；Agent Runtime 放独立 Worker。
5. Session 必须归属 Project；Task 关联可空。
6. Session、Execution、Attempt 是三个不同实体。
7. Agent 汇报状态与平台认可状态分离。
8. Task Tree 为默认视图；DAG 仅用于真实依赖。
9. Revision 追加式；不覆盖历史。
10. MCP 是外部 Agent 协作入口，不是 Runtime Session Control API。
11. 不做通用 Agent Loop；不承诺对所有外部 Agent 强制暂停/恢复。
12. 不要求旧版 API、数据库、前端和 Adapter 兼容；迁移/导出策略仍需明确，禁止静默丢失用户数据。

---

# 1. 全阶段通用开发规约

## 1.1 每阶段必须交付

每个阶段结束时，开发 Agent 必须提交：
- 变更文件清单；
- 本阶段实现内容和明确未实现内容；
- 数据库迁移说明；
- API / MCP 契约变化；
- 自动化测试命令和真实执行结果；
- Windows PowerShell 下的启动/测试结果；
- 关键页面截图（涉及 UI 时至少 360px 手机和 1440px 桌面）；
- 风险、已知缺陷和下一阶段前置条件；
- 独立 Git commit，提交信息说明阶段编号；
- 不得只写“测试通过”，必须附命令、退出码和测试摘要。

## 1.2 代码边界

建议按业务模块组织，不要按技术类型把所有逻辑堆成巨型目录。目录名可根据现有仓库调整，但职责应保持：

```text
backend/
  app/
    api/                 # REST 路由
    core/                # 配置、日志、安全、异常
    domain/               # 领域实体、状态机、领域规则
    application/          # 用例编排、事务边界
    infrastructure/
      database/           # SQLAlchemy、Alembic、Repository
      events/              # 事件持久化与分发
      connectors/          # Runtime Connector
      mcp/                 # MCP Server
      workers/             # Worker Supervisor、IPC
    modules/
      projects/
      tasks/
      sessions/
      executions/
      decisions/
      artifacts/
  workers/
    base_worker/
    mock_worker/

frontend/
  src/
    app/                  # Router、应用壳
    pages/
    features/
      projects/
      tasks/
      sessions/
      decisions/
      executions/
      artifacts/
    components/
    lib/
    styles/

tests/
  unit/
  integration/
  contract/
  e2e/
  fixtures/
docs/
scripts/
```

以上是目标职责结构，不要求机械照搬现有仓库路径。Phase 0 必须先输出真实文件映射。

## 1.3 统一工程规则

- 所有外部输入使用 Pydantic schema 校验。
- API 层不直接写 SQL；通过 Application Service / Repository。
- 领域状态迁移由领域服务校验，前端不能绕过。
- 业务状态变化与对应 Event 在同一数据库事务内提交。
- 不在数据库事务中等待 Agent、网络或用户操作。
- 所有跨 Project 关联必须在服务端验证。
- 所有写 API 支持幂等策略或明确的冲突响应。
- 所有时间统一存 UTC，前端按设备本地时区显示。
- 主键使用稳定 ID（建议 UUID/ULID 二选一并统一）；外部 Runtime ID 只作为外部引用。
- 日志不得记录 API Key、Token、完整 Secret、敏感 Prompt。
- Mock 数据必须显式标注，不能伪装成真实 Runtime。
- 未经真实测试的 Connector capability 标记 `unverified`。

---

# Phase 0：仓库复核与架构冻结

<badge>阶段目标：避免基于过时认知重写</badge>

## 0.1 输入

- 当前指定功能分支；
- 当前仓库源码和最近提交；
- 架构决策书 v1.1；
- UI 原型与交互规格书 v1.0。

## 0.2 开发步骤

1. 获取当前分支、HEAD commit、工作区状态；确认不是 `main`。
2. 检查 README、依赖清单、Python/Node 版本、启动脚本、环境变量。
3. 盘点后端入口、API 路由、数据库访问、WebSocket/SSE、Agent Adapter、进程管理。
4. 盘点前端框架、入口、路由、页面、样式、PWA、API 客户端。
5. 盘点测试：区分单元测试、模拟测试、集成测试、真实 Runtime 测试。
6. 本地执行现有测试和启动流程；无法执行的项目明确记录原因。
7. 将现有功能映射到目标领域模型：可复用、需重构、应删除、未知。
8. 检查旧 SQLite 数据内容和敏感数据，不做任何破坏性迁移。
9. 输出 ADR 列表，至少覆盖技术栈、领域模型、Session 归属、Execution、MCP、Worker、Revision、事件和安全。
10. 输出阶段依赖图和风险登记表。

## 0.3 交付物

- `docs/implementation/current-state-audit.md`
- `docs/implementation/target-file-map.md`
- `docs/adr/` 下的初始 ADR
- `docs/implementation/risk-register.md`
- 基线测试报告

## 0.4 验收门槛

- 能明确说出当前哪些功能真实可运行；
- 目标页面、API、数据库实体与当前代码有映射；
- 关键风险没有被当作已解决；
- 形成明确的删除/重构范围；
- 未修改业务代码或数据库结构。

**Gate 0：** 仓库审查报告通过后，才允许进入基础工程改造。

---

# Phase 1：技术验证与工程基线

<badge>阶段目标：先验证最可能阻塞架构的技术风险</badge>

此阶段不追求产品页面完整，只建立最小实验工程和可重复测试。

## 1.1 Windows / Python / Node 基线

1. 确定 Python 版本：目标 Python 3.12+，检查所有关键依赖 wheel 和 Runtime SDK 兼容性。
2. 确定 Node LTS 版本，锁定 package manager。
3. 统一依赖锁文件；禁止开发机依赖未锁定的全局包。
4. 提供 PowerShell 一键启动和测试脚本。
5. 验证项目路径含空格、中文字符时可正常运行。
6. 验证 Windows 用户目录、盘符路径、UNC 路径处理策略。

## 1.2 SQLite 技术验证

1. 建立独立实验库，开启 WAL 和 `foreign_keys=ON`。
2. 验证并发读写、短事务、锁冲突和有限重试。
3. 验证 SQLite Backup API 在线备份和恢复。
4. 验证进程异常退出后 WAL 恢复。
5. 验证事务内写业务记录和 Event 的原子性。
6. 验证 schema migration 从空库升级。
7. 记录 SQLite 连接管理策略：每个请求/任务的 session 生命周期、超时和关闭。

## 1.3 Worker JSONL IPC 验证

1. 编写最小 Worker，stdin 读取 JSONL，stdout 只输出 JSONL，stderr 输出诊断日志。
2. 每条消息包含 `schema_version`、`request_id`、`execution_id`、`type`、`payload`。
3. 验证大消息、Unicode、多行文本、连续事件、EOF、非法 JSON。
4. 验证 Worker 正常退出、非零退出、崩溃、无响应、父进程退出。
5. 验证取消请求与 Worker 退出时序。
6. 验证 Windows 子进程编码、缓冲、信号和进程树清理。
7. 定义 stdout 协议污染检测：任何非 JSON 输出都触发协议错误。

## 1.4 MCP 技术验证

1. 建立最小 AgentDeck MCP Server，只提供只读 `project_context_get` 和 `task_get`。
2. 建立最小 Project/Task fixture。
3. 使用用户实际的 Antigravity IDE/CLI 配置连接本机 MCP Server。
4. 验证初始化、工具列表、参数 schema、调用、错误响应、断开重连。
5. 验证 Token 不存在、过期、错误 Project scope 时拒绝访问。
6. 验证 MCP Server 关闭后客户端能识别断连。
7. 保存真实运行日志和版本信息，敏感字段脱敏。

## 1.5 验收门槛

- Windows 下 JSONL Worker 连续运行稳定；
- SQLite Backup API 恢复测试通过；
- Antigravity 实际调用 MCP 工具成功；
- 不把 SDK Preview 或内部私有 RPC 当作稳定依赖；
- 所有实验均可通过脚本重复。

**Gate 1：** MCP smoke test 和 Worker IPC 未通过，不进入大规模 Runtime 开发。

---

# Phase 2：基础工程与开发体验

## 2.1 后端骨架

1. 建立 FastAPI 应用工厂和配置管理。
2. 加入 Pydantic v2 请求/响应模型。
3. 加入 SQLAlchemy 2.x Async 或同步方案评估；SQLite 驱动和事务行为实测后统一，不混用两套访问模式。
4. 加入 Alembic，建立首个 schema migration。
5. 建立统一异常结构：`code/message/details/request_id`。
6. 建立结构化日志和 request correlation ID。
7. 建立健康检查：`/health/live`、`/health/ready`。
8. 建立 API 版本前缀，例如 `/api/v1`。
9. 建立依赖注入和事务边界。
10. 建立统一时间、ID、分页和排序规范。

## 2.2 前端骨架

1. 建立 React + TypeScript + Vite。
2. 配置严格 TypeScript（避免随意 `any`）。
3. 建立路由和 404 / Error Boundary。
4. 建立 API Client，统一超时、错误解析和请求 ID。
5. 配置 TanStack Query；Zustand 只存 UI 状态，不复制服务端实体。
6. 建立暗色 Token、响应式布局和基础组件。
7. 配置 ESLint、Prettier、类型检查和构建。
8. 建立 PWA manifest、图标和基础离线提示。
9. 根据 UI 规格建立导航壳和页面占位，不做假数据业务闭环。

## 2.3 Mock Connector

1. 实现 `MockConnector`，具备固定能力声明。
2. 支持创建/恢复 Mock Session、开始 Execution、事件流、取消、状态查询。
3. 支持可配置延迟、失败、断连、权限请求和超时。
4. Mock 事件必须标注 `source=mock`。
5. 建立统一 Connector Contract Tests，Mock 先通过。

## 2.4 CI 与开发脚本

1. 后端 lint、类型检查、单测。
2. 前端 lint、类型检查、构建。
3. migration 空库升级测试。
4. Windows PowerShell 脚本：安装、启动、测试、停止。
5. Linux/macOS 脚本先保持同等核心命令。
6. `.env.example` 不含真实 Secret。
7. 提供 Demo Project seed，明确仅用于开发环境。

## 2.5 验收

- 新开发者按文档可在 15 分钟内启动；
- 无 API Key 也能通过 Mock 使用主要 UI；
- 后端和前端 CI 全绿；
- 空 SQLite 可完成迁移；
- Windows 启停脚本可重复运行。

---

# Phase 3：核心领域模型与数据库

## 3.1 先定义不变量

必须在代码和数据库约束中体现：

1. Session 必须有 `project_id`。
2. Session 的 `task_id` 可空；若非空，Task 必须属于同一 Project。
3. Task 必须属于 Project；父 Task 必须属于同一 Project。
4. Step 必须属于 Task。
5. Execution 必须属于 Project；引用的 Task/Step/Session 必须属于同一 Project。
6. Attempt 必须属于 Execution。
7. Decision 必须属于 Project；其引用对象必须属于同一 Project。
8. Artifact 必须属于 Project；可选引用的 Task/Step/Execution 必须同 Project。
9. 外部 Runtime ID 不得用作平台主键。
10. 历史 Revision 不得原地覆盖。

## 3.2 建表顺序

建议初始核心表：
1. `projects`
2. `tasks`
3. `task_revisions`
4. `steps`
5. `step_revisions`
6. `sessions`
7. `task_dependencies`
8. `events`
9. `decisions`
10. `artifacts`

Execution / Attempt 在 Phase 8 引入也可，但领域 ID 和引用方式须提前评审。

辅助表按实际需要添加：
- `agent_runtime_definitions`
- `agent_runtime_instances`
- `model_providers`
- `model_profiles`
- `audit_logs`
- `settings`

不要为了预想扩展一次性建大量空表。

## 3.3 Revision 设计

1. Task 有自己的 revision number。
2. Step 有自己的 revision number。
3. 创建 Revision 时保存该对象的必要内容快照或字段差异；方案必须明确采用哪一种。
4. Execution 创建时记录 Task revision、Step revision。
5. 仅当复现需要时保存执行输入快照，不复制整个 Project。
6. 修改 Revision 使用乐观并发控制：客户端提交 `expected_revision`。
7. Revision 恢复通过创建新 Revision 完成，不能覆盖历史。
8. Revision 修改后只计算潜在下游影响，不自动触发执行。

## 3.4 任务状态机

1. Task 状态与 Step 状态分别定义。
2. 状态迁移放领域服务统一校验。
3. `needs_revalidation` 是附加标记，不应粗暴覆盖原状态。
4. Agent 报告与平台认可状态分离。
5. `completed` 应有平台验收依据，不可仅凭 Agent 文本自动置完成。
6. 每次状态变化写 Event。
7. 非法迁移返回稳定错误码。

## 3.5 迁移与数据策略

1. 使用 Alembic 管理 schema version。
2. 开发环境允许重建数据库；生产升级先备份。
3. 旧数据不做隐式丢弃；先提供只读盘点报告。
4. 如果旧数据不适配新模型，提供显式导出/导入或归档方案。
5. 不为旧 API 和旧 UI 保持永久兼容。
6. 每个 migration 都有 upgrade/downgrade 策略说明；无法安全 downgrade 时明确标注。

## 3.6 验收

- 领域不变量单元测试全部通过；
- 外键和跨 Project 关联约束有效；
- Revision 并发冲突可检测；
- 删除/归档不会级联清空历史；
- 从空库升级和备份恢复成功。

---

# Phase 4：核心 REST API 与应用服务

## 4.1 Project API

建议接口：
- `POST /api/v1/projects`
- `GET /api/v1/projects`
- `GET /api/v1/projects/{project_id}`
- `PATCH /api/v1/projects/{project_id}`
- `POST /api/v1/projects/{project_id}/archive`
- `POST /api/v1/projects/{project_id}/unarchive`

步骤：
1. 定义 schema 和错误码。
2. 校验名称、根目录、路径规范化。
3. 根目录去重和可访问性检查。
4. 建立 Project Summary 聚合查询。
5. 增加归档/取消归档，不提供物理删除。
6. 测试路径中空格、中文、UNC、符号链接等情况。

## 4.2 Task / Step API

建议接口：
- `POST /projects/{id}/tasks`
- `GET /projects/{id}/tasks`
- `GET /tasks/{task_id}`
- `PATCH /tasks/{task_id}`
- `POST /tasks/{task_id}/archive`
- `POST /tasks/{task_id}/status`
- `POST /tasks/{task_id}/steps`
- `GET /tasks/{task_id}/steps`
- `PATCH /steps/{step_id}`
- `POST /steps/{step_id}/status`
- `GET /steps/{step_id}/revisions`
- `POST /steps/{step_id}/revisions`

步骤：
1. 创建 Task，支持根任务和子任务。
2. 校验 parent 同 Project。
3. 提供树形查询，避免 N+1 查询。
4. 提供分页/搜索/筛选/排序。
5. 实现状态迁移。
6. 实现 Step CRUD。
7. 实现 Revision 追加。
8. 实现排序保存与冲突检测。
9. 归档不级联删除。
10. 输出 OpenAPI 契约。

## 4.3 Session API

- `POST /projects/{project_id}/sessions`
- `GET /projects/{project_id}/sessions`
- `GET /sessions/{session_id}`
- `PATCH /sessions/{session_id}`
- `POST /sessions/{session_id}/end`

规则：
- project_id 必填且服务端校验；
- task_id 可空；
- 任务关联只能关联同 Project Task；
- Session 归属 Project 不允许通过普通 PATCH 改变；
- Session 状态不能推断 Execution 是否成功。

## 4.4 事件查询 API

- `GET /projects/{project_id}/events?after={event_id}`
- `GET /tasks/{task_id}/events`
- 支持稳定 event_id、递增 sequence、分页；
- 事件 payload 有 schema_version；
- 默认业务事件，技术诊断独立接口。

## 4.5 验收

- OpenAPI 与实际实现一致；
- API 集成测试覆盖跨 Project 越权；
- 幂等和冲突返回可预测；
- 前端可仅靠 REST 完成 Project/Task/Step 基础操作。

---

# Phase 5：前端工作台基础与任务树

## 5.1 基础设计系统

1. 落地暗色主题 Token。
2. 实现 PageHeader、StatusPill、TaskNode、StepRow、DecisionCard、ArtifactCard。
3. 实现 Dialog、Drawer、Toast、Skeleton、EmptyState、OfflineBanner。
4. 建立移动端底部导航和桌面左侧导航。
5. 统一状态文案和状态图标。
6. 支持 320/360/768/1440px。

## 5.2 页面实施顺序

1. P00 启动检测和健康状态。
2. P01 项目选择/创建。
3. P02 工作总览（先展示 Project/Task 聚合信息）。
4. P03 项目列表。
5. P04 项目详情/任务树。
6. P05 任务详情基础版。
7. P06 Step 编辑和 Revision 历史。

## 5.3 Task Tree 交互

1. 首屏默认显示一级 Task。
2. 展开后显示子任务/Step。
3. 标题点击进入详情，展开按钮只展开。
4. 状态筛选与搜索由服务端支持，UI 不伪造。
5. 默认手动排序；拖动排序显式进入编辑模式。
6. 拖动改变父子层级前二次确认。
7. 保存排序失败恢复原位置并提示。
8. 节点菜单包含打开、新建子任务、编辑、状态、优先级、归档、执行记录。
9. `needs_revalidation` 独立显示。
10. 无依赖关系时不展示 DAG 入口。

## 5.4 任务详情

先完成概览/步骤/动态/交付物 Tab 框架；没有后端数据的 Tab 显示明确空状态，不填充假数据。
“与 Agent 讨论”按钮在 Session API 未完成前禁用或标注未开放。

## 5.5 验收

- 任务树和任务详情与 UI 规格书一致；
- 移动端单手可完成查看、创建、编辑、状态操作；
- 320px 无横向滚动；
- 所有写操作具备 loading/success/error/conflict；
- 刷新后状态与服务端一致。

---

# Phase 6：AgentDeck MCP Server

<badge>关键产品里程碑：外部 Agent 第一次真正使用平台</badge>

## 6.1 MCP 工具设计

P0 工具：
1. `project_context_get`
2. `task_get`
3. `task_report_progress`
4. `step_update`
5. `checkpoint_request`
6. `decision_get`
7. `artifact_register`
8. `task_revision_get`

## 6.2 工具开发步骤

1. 为每个工具编写 JSON Schema。
2. 设计统一错误码：未授权、Project 不匹配、资源不存在、版本冲突、状态不允许、幂等重复。
3. 设计 MCP 身份凭证：Token 绑定 Project 和最小权限。
4. Token 必须支持过期、撤销和轮换。
5. 服务端从 Token 解析作用域，不信任 Agent 传入的 project_id。
6. 工具调用记录审计事件。
7. 所有写操作接收 idempotency key。
8. `project_context_get` 只返回当前工作所需上下文，不返回整个项目数据库。
9. `task_report_progress` 只记录 Agent 报告，不自动把平台 Task 标记完成。
10. `step_update` 需要 expected_revision，避免覆盖用户修改。
11. `checkpoint_request` 创建持久化 Decision。
12. `decision_get` 返回 Decision 当前状态和用户选择。
13. `artifact_register` 只登记已验证的 Project 内 Artifact。
14. 为工具响应定义稳定结构和版本号。

## 6.3 外部 Agent 端到端验证

1. 在 AgentDeck 创建 Project 和 Task。
2. 生成最小权限 MCP Token。
3. 在 Antigravity 配置 AgentDeck MCP Server。
4. Agent 调用 `project_context_get`。
5. Agent 调用 `task_get`。
6. Agent 汇报进度。
7. Agent 创建 Decision。
8. 用户在手机/桌面收件箱做决定。
9. Agent 再次查询 Decision。
10. Agent 登记 Artifact。
11. AgentDeck UI 展示业务事件、Decision 和 Artifact。

## 6.4 验收

- Antigravity 实际调用成功，不以 Mock 代替；
- Project Token 无法访问其他 Project；
- 重复写请求不会重复创建资源；
- Agent 报告不会越权修改平台验收状态；
- MCP 断线后可重连；
- 工具 schema 与文档一致。

---

# Phase 7：Event / Decision / 恢复机制

## 7.1 Event Store

1. 定义 Event 基础字段：event_id、sequence、project_id、entity_type、entity_id、event_type、payload、schema_version、created_at、actor_type、actor_id。
2. Event 与业务状态写入同一事务。
3. sequence 在 Project 作用域内严格递增，或采用全局递增并说明策略。
4. event_id 唯一约束。
5. 事件重复写入幂等。
6. 提供按 last_event_id 拉取增量接口。
7. 事件 payload 只保存业务所需信息，避免 Secret 和完整敏感 Prompt。
8. 事件类型定义文档化，禁止随意拼写字符串。

## 7.2 Decision 持久化

1. Decision 先落库，再通知 Agent/UI。
2. 定义 pending、resolved、expired、cancelled 状态。
3. 选项 schema 固定且版本化。
4. 保存用户选择、备注、决策时间和决策来源。
5. 同一 Decision 只允许一次成功 resolve。
6. 重复提交使用幂等键。
7. 服务启动时扫描 pending Decision。
8. 恢复 UI 待处理列表。
9. 对接 Runtime 的审批恢复必须单独验证；如果 Runtime 无法恢复回调，显示需要重新发起，不伪装成原审批仍然有效。

## 7.3 SSE / WebSocket

1. 选择一种主推送机制；若 REST 增量 + SSE 足够，不同时维护两套复杂实现。
2. 推送只做通知，不作为唯一事件存储。
3. 客户端断线显示 reconnecting。
4. 重连后用 last_event_id 拉增量。
5. 重复事件按 event_id 去重。
6. 乱序事件按 sequence 处理。
7. 无法确定状态时展示 unknown/stale，不自行猜测。

## 7.4 验收

- 服务重启后 pending Decision 仍可见；
- 事件断线补拉无遗漏；
- 重复事件不重复产生 UI 记录；
- 业务表与 Event 不会出现一边成功一边失败；
- 无法恢复 Runtime 审批时有明确人工处理路径。

---

# Phase 8：Execution / Attempt / Worker

## 8.1 领域定义

- Session：持续对话身份。
- Execution：一次明确的工作请求。
- Attempt：一次实际运行/恢复尝试。
- Agent Turn 不是 Attempt。
- 一个 Session 可有多个 Execution；一个 Execution 可有多个 Attempt。

## 8.2 数据与状态

1. Execution 记录 Project、Task、可选 Step、Session、Task/Step Revision。
2. Attempt 记录 Connector、Runtime、外部执行 ID、开始/结束时间、退出码、错误摘要。
3. Execution 状态与 Attempt 状态分开。
4. 失败重试创建新 Attempt，不覆盖旧 Attempt。
5. 取消请求和取消完成分开记录。
6. 不支持取消的 Connector 返回 unsupported。
7. 无法确认 Runtime 状态时标记 unknown，不假装失败或成功。

## 8.3 Worker Supervisor

1. Main Process 创建 Worker。
2. 启动参数、cwd、环境变量白名单显式设置。
3. Secret 通过安全 IPC 或短期环境注入，不写入日志。
4. Worker stdout 只用于 JSONL 协议。
5. stderr 用于诊断并做大小限制。
6. 建立心跳/超时。
7. Worker 崩溃时记录退出码并结束当前 Attempt。
8. 重启时核对 Runtime 是否仍在执行。
9. 未确认前标记 unknown。
10. 只有确认原执行已结束/不可恢复后才允许新 Attempt。

## 8.4 Connector Capability Manifest

每项能力包含：
- capability；
- support_status：supported / unsupported / unverified；
- evidence：official_docs / integration_test / inferred；
- runtime_version；
- connector_version；
- verified_at；
- limitations。

## 8.5 验收

- Worker 正常执行、异常退出、超时、取消、父进程退出均有测试；
- 不支持的能力明确返回；
- Execution/Attempt 历史完整；
- 不会因为 Worker 重启而重复执行副作用操作；
- Windows 真实子进程行为已验证。

---

# Phase 9：第一个托管 Runtime

## 9.1 选择原则

Claude 和 OpenCode 二选一，按用户真实使用频率、官方 SDK/API 成熟度、Windows 支持和审批/取消/恢复能力决定。不能因为某个 Runtime 容易接就默认选择。

## 9.2 Claude 路线

1. 验证官方 Agent SDK 安装和 Windows 支持。
2. 验证创建 Session、恢复 Session。
3. 验证事件流和结构化消息。
4. 验证 `canUseTool` / 权限回调。
5. 验证拒绝、批准、超时和取消。
6. 验证工具调用事件映射。
7. 验证 SDK 异常、进程退出和重启恢复。
8. 将 SDK 特有字段隔离在 Claude Connector 内。

## 9.3 OpenCode 路线

1. 启动官方 `opencode serve`，默认仅绑定 loopback。
2. 配置 Basic Auth/安全访问。
3. 验证 OpenAPI 契约。
4. 验证 Session、Message、Prompt、SSE。
5. 验证 Permission reply、Abort、Diff。
6. 禁止直接写 OpenCode 内部 SQLite。
7. HTTP 调用使用异步客户端。
8. 版本升级通过契约测试。

## 9.4 Connector Contract Tests

每个 Runtime 测试：
- initialize / shutdown；
- health check；
- capabilities；
- session create / resume；
- execution start；
- streaming；
- status query；
- permission request / reply；
- cancel；
- crash recovery；
- duplicate command；
- unsupported behavior。

## 9.5 验收

- 至少一个托管 Runtime 真正完成一次任务；
- Session/Execution/Attempt 记录正确；
- 人工审批从 UI 到 Runtime 回调真实闭环；
- 取消能力按实测结果呈现；
- SDK/API 版本固定并记录。

---

# Phase 10：Artifact 与 Revision 影响分析

## 10.1 Artifact

1. 定义 Artifact 元数据，不将所有日志都当交付物。
2. Artifact 必须归属 Project。
3. 可选关联 Task/Step/Execution/Attempt。
4. 记录来源、版本、摘要、MIME、大小、存储位置和校验值。
5. 文件必须位于 Project root allowlist 或平台管理的安全存储目录。
6. 路径规范化并检查 symlink escape。
7. 文件移动/删除由平台服务执行，不接受任意 Agent 路径。
8. 预览和下载通过受控 API。
9. Artifact 历史版本可追溯。

## 10.2 Revision 影响分析

1. 修改 Task/Step 时生成新 Revision。
2. 计算直接依赖下游对象。
3. 只标记潜在受影响对象 `needs_revalidation`。
4. 保留其原平台状态和历史 Execution。
5. 展示影响范围和触发原因。
6. Agent 可提交复核建议：无需重跑 / 建议重跑 / 需要人工判断。
7. 平台不自动重跑。
8. 用户可确认复核结论。
9. Execution 记录使用的 Revision 引用。
10. 支持查看“当前版本”和“执行时版本”的差异。

## 10.3 验收

- Artifact 路径越界被拒绝；
- 产物能追溯到具体 Execution 和 Revision；
- Step 修改不会覆盖旧记录；
- 下游只标记复核，不自动执行；
- Agent 的复核建议与用户最终决定分开记录。

---

# Phase 11：完整前端体验

依照 UI 规格书实施，不在此重新发明视觉设计。

## 11.1 页面顺序

1. P02 工作总览；
2. P03 项目列表；
3. P04 项目详情/任务树；
4. P05 任务详情；
5. P06 Step 编辑/Revision；
6. P08 收件箱；
7. P07 决策详情；
8. P09 Session 列表；
9. P10 Session 详情；
10. P11 Artifact；
11. P12 活动记录；
12. P13 依赖关系图；
13. P14 设置。

## 11.2 关键交互

- 首页待决策优先于执行中。
- 决策详情展示 Agent 建议、影响和选项；不推断未知影响。
- 任务详情展示当前 Step、Revision 和交付物。
- Session 可不关联 Task，但必须关联 Project。
- Execution 失败不自动把 Task 标为失败。
- Attempt 重试不覆盖旧记录。
- 断线后恢复事件，不重复执行写操作。
- PWA 离线只读缓存；离线禁用 Decision、Prompt 和任务写操作。
- 移动端触控热区至少 44px；适配 Safe Area。

## 11.3 视觉与交互验收

- 360px 手机截图；
- 1440px 桌面截图；
- 320px 无横向溢出；
- iOS Safe Area 正常；
- 键盘可访问主要流程；
- 页面状态覆盖加载、空、错误、离线、无权限、数据过期、实时断开；
- 所有关键按钮真实工作，不保留无动作按钮。

---

# Phase 12：第二 Runtime、Codex 与 ACP

## 12.1 第二 Runtime

目标不是增加数量，而是证明 Connector 抽象没有绑定第一个厂商。
1. 选择第二种真实 Runtime。
2. 复用统一领域服务。
3. 仅新增 Connector、Capability Manifest、事件映射。
4. 不复制 Session/Execution/Decision 数据库逻辑。
5. 执行同一套 Contract Tests。
6. 比较能力差异并展示 unsupported。

## 12.2 Codex App Server

1. 使用官方 JSON-RPC 2.0 stdio。
2. 固定 Codex 版本。
3. 处理 newline-delimited JSON。
4. 验证 thread start/resume、turn、approval、streaming、interrupt。
5. 注意官方实验性/生产限制。
6. 未通过契约和真实测试前标记 experimental。

## 12.3 ACP

1. 只实现明确支持 ACP 的 Runtime。
2. 采用已发布稳定协议版本。
3. 覆盖 initialize、session、prompt、update、permission、cancel。
4. 不依赖 draft v2。
5. 不要求 MCP 与 ACP 互相替代。

## 12.4 验收

- 第二 Runtime 通过同一 Contract Tests；
- 核心领域代码没有厂商特定分支污染；
- Capability 差异准确展示；
- Codex/ACP 标记与实际验证成熟度一致。

---

# Phase 13：安全加固、发布与运维

## 13.1 安全

1. 收紧 CORS，禁止生产环境通配来源 + credentials。
2. WebSocket/SSE 统一认证与 Origin 校验。
3. Tailscale 网络不等于应用授权。
4. MCP Token 最小权限、短期、可撤销、审计。
5. Project scope 服务端验证。
6. Project 文件路径 allowlist。
7. symlink、UNC、盘符、路径大小写处理测试。
8. Secret 存储使用系统安全存储或加密配置；日志脱敏。
9. 防止任意 Shell / 任意 SQL / 任意文件读取工具暴露。
10. 对高风险操作做二次确认和审计。
11. 设置请求体大小、上传大小、超时和并发上限。
12. 依赖漏洞扫描和锁文件审查。

## 13.2 备份恢复

1. 使用 SQLite Backup API。
2. 支持手动备份。
3. 支持定期备份策略（可选）。
4. 恢复前验证数据库完整性。
5. 恢复流程先停止写入并创建当前备份。
6. 恢复后执行 schema version 检查。
7. 备份文件加密/权限限制。
8. 定期做真实恢复演练，不只验证备份文件存在。

## 13.3 发布

1. 锁定 Python、Node、依赖版本。
2. 提供 Windows 安装/升级/卸载说明。
3. 提供 Linux/macOS 部署说明。
4. 提供服务端口、Tailscale 访问、安全配置说明。
5. 提供日志目录和诊断包导出。
6. 提供数据库迁移失败恢复步骤。
7. 提供版本变更日志。
8. 做干净机器安装验证。
9. 旧版数据迁移如不支持，明确导出和归档路径。

## 13.4 发布验收

- 干净 Windows 环境可安装运行；
- SQLite 备份恢复演练成功；
- 安全测试全部通过；
- 核心产品主流程 E2E 通过；
- 无高危未处理漏洞；
- 已知限制、Runtime 兼容矩阵、升级回滚说明完整。

---

# 2. 全链路验收场景（最终必须通过）

## 场景 A：外部 Agent 协作主链路

1. 用户创建 Project。
2. 用户创建 Task 和 Step。
3. 用户生成绑定 Project 的 MCP Token。
4. Antigravity 通过 MCP 读取 Project/Task 上下文。
5. Agent 汇报进度。
6. Agent 请求用户 Decision。
7. Decision 持久化并出现在收件箱。
8. 用户从手机批准/拒绝/要求补充信息。
9. Agent 读取 Decision 结果并继续。
10. Agent 登记 Artifact。
11. 用户查看 Artifact 和任务动态。
12. 用户验收 Task。

## 场景 B：Step 修改与下游复核

1. Step v1 被 Execution E1 使用。
2. 用户修改 Step，生成 v2。
3. E1 仍引用 v1。
4. 下游依赖对象标记 `needs_revalidation`。
5. Agent 分析影响并提交复核建议。
6. 用户确认无需重跑或要求重跑。
7. 新执行引用 v2。
8. 历史版本和执行结果仍可查看。

## 场景 C：托管 Runtime 崩溃恢复

1. 创建 Execution。
2. Worker 创建 Attempt 1。
3. Runtime 执行中 Worker 崩溃。
4. Supervisor 记录退出码和最后心跳。
5. 系统查询 Runtime 状态。
6. 无法确认则标记 unknown。
7. 不自动启动重复副作用操作。
8. 用户/策略确认后创建 Attempt 2。
9. 两次 Attempt 都可追溯。

## 场景 D：服务重启与待决策恢复

1. Agent 创建 Decision。
2. Decision 写入 SQLite。
3. 服务重启。
4. 服务启动扫描 pending Decision。
5. 收件箱重新显示。
6. Agent/Runtime 恢复能力单独检测。
7. 不支持恢复时提供重新发起路径。

---

# 3. 不允许的实现捷径

1. 不允许把 Session、Execution、Attempt 合并成一张表。
2. 不允许让 Session.project_id 为空。
3. 不允许因为 Agent 说“完成”就自动验收 Task。
4. 不允许把 MCP 当成 Runtime 生命周期控制协议。
5. 不允许把 Antigravity 私有 Language Server RPC 当核心集成。
6. 不允许直接写 OpenCode 内部数据库。
7. 不允许把所有 Agent 强制套入同一种底层协议。
8. 不允许将所有任务强制转成 DAG。
9. 不允许 Step 修改后自动重跑下游。
10. 不允许只在内存保存待决策状态。
11. 不允许复制 SQLite 主库/WAL/SHM 文件冒充一致性备份。
12. 不允许把 Worker 的普通日志写进 stdout JSONL 通道。
13. 不允许用 Mock 测试结果冒充真实 Runtime 集成通过。
14. 不允许把 Token、Secret、敏感 Prompt 写入普通日志。
15. 不允许前端直接访问 SQLite 或 Agent 内部数据库。
16. 不允许页面保留看似可点击但没有实现的按钮。
17. 不允许在 `main` 上直接开发或提交。
18. 不允许未经确认删除用户数据。

---

# 4. 给开发 Agent 的总控 Prompt

```text
你负责实施 AgentDeck。请把以下文档作为不可随意偏离的基线：
1. AgentDeck 最终架构复核与重构决策书 v1.1
2. AgentDeck 全产品页面原型与交互规格书 v1.0
3. AgentDeck 分阶段开发实施计划 v1.0

你的任务不是一次性重写整个仓库，而是严格按实施计划分阶段推进。

执行规则：
- 先完成 Phase 0 仓库复核，不要直接改代码。
- 只在指定功能分支工作，绝不修改 main。
- 每阶段开始前列出目标、依赖、具体文件和风险。
- 每阶段完成后运行对应测试，提供命令、退出码和结果。
- 每阶段独立提交，提交信息包含 Phase 编号。
- 未经真实验证的能力标记 unverified。
- 不得用 Mock 结果冒充真实 Runtime 测试。
- 不得擅自改变领域不变量和架构 ADR。
- 发现架构冲突时先写出冲突、影响和备选方案，等待确认。
- 不要在一个阶段顺手实现后续阶段的大量功能。
- 不修改或删除用户数据；任何破坏性迁移必须先报告并等待确认。
- 所有 UI 必须遵守 UI 规格书，提供手机和桌面截图。
- 每阶段输出变更文件清单、测试结果、截图、未完成项和下一阶段计划。

当前只执行 Phase 0。完成仓库审查后停止，提交审查报告，不开始 Phase 1，等待用户确认。
```

---

# 5. 建议阶段提交规范

| 阶段 | Commit 前缀示例 |
|---|---|
| Phase 0 | `docs(audit): complete current-state architecture audit` |
| Phase 1 | `test(spike): validate sqlite worker and mcp foundations` |
| Phase 2 | `chore(platform): establish backend frontend and ci baseline` |
| Phase 3 | `feat(domain): add project task step and revision models` |
| Phase 4 | `feat(api): add project task step and session endpoints` |
| Phase 5 | `feat(ui): build project workspace and task tree` |
| Phase 6 | `feat(mcp): expose agent collaboration tools` |
| Phase 7 | `feat(events): persist decisions and recover event streams` |
| Phase 8 | `feat(execution): add execution attempts and worker supervisor` |
| Phase 9 | `feat(connector): integrate first managed runtime` |
| Phase 10 | `feat(artifacts): add artifact registry and revision impact analysis` |
| Phase 11 | `feat(ui): complete inbox session and artifact workflows` |
| Phase 12 | `feat(connector): add second runtime and protocol clients` |
| Phase 13 | `hardening(release): secure backup and package AgentDeck` |

---

# 6. 最终完成定义（Definition of Done）

只有同时满足以下条件，才能称为 AgentDeck 重构完成：

- 产品定位和领域不变量落实到代码；
- Project/Task/Step/Revision 可持久化管理；
- Session 必须属于 Project，Task 可空；
- Execution/Attempt 分离且历史完整；
- 外部 Agent 能通过 MCP 读写受授权的协作数据；
- Decision 能持久化、人工处理并在服务重启后恢复；
- 至少一个托管 Runtime 真实通过 Session、Streaming、Permission、Cancel/Resume 能力验证（不支持项明确标注）；
- Artifact 可追溯到来源和 Revision；
- Step 修改能标记下游复核但不会自动重跑；
- 任务树、任务详情、收件箱、Session、Artifact 页面可用；
- 手机和桌面关键流程 E2E 通过；
- Worker、事件、备份、恢复、安全测试通过；
- Windows 是真实验证平台，macOS/Linux 至少完成核心兼容测试；
- 有安装、升级、备份恢复、诊断和故障处理文档；
- 所有未实现和不支持能力如实展示。

**最终产品验收标准不是“支持了多少 Agent”，而是：一个 Project 的工作能否在多个 Agent 之间连续推进，关键决策有人负责，版本和执行可追溯，产物可信，失败可恢复。**
