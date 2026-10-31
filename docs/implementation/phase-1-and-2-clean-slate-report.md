# AgentDeck Clean-Slate Phase 1 & Phase 2 实施与验收报告

## 1. 概述与架构基线

遵照《AgentDeck Clean-Slate 最终重构授权与 Phase 2 启动指令》，本次重构彻底废止了新旧双轨过渡方案，以 `AgentDeck_Architecture_and_Roadmap_v1.3.md` 为唯一最终架构基线，完成了 **Clean-Slate Phase 1（核心领域模型与 21 张规范表重建）** 与 **Phase 2（`/api/v1/*` REST API + `frontend/` React + TypeScript + Vite 核心项目管理与交互式决策 UI）**。

- **不再保留**：旧数据库业务数据（287 条旧会话）、旧 `t_*` 兼容表、旧 `UnifiedSession` 聊天架构、旧逆向/私有适配器（`antigravity_*`、`claude_*`、`opencode_*`）、旧 `frontend/` 原生 ES Modules 代码及 `backend/v2/` 临时过渡目录。
- **一级正统架构**：后端统一收敛于 `backend/`，API 统一挂载于 `/api/v1/*`，前端直接在 `frontend/` 建设 React 19 + TypeScript + Vite 6 工程。
- **外部边界守卫**：未集成 CCSwitch，未读取或修改任何外部 Agent 配置/数据库/凭证，未提前引入 MCP Server（Phase 4）、Runtime SDK 控制（Phase 6/7）或 Worker（Phase 9 可选）。

---

## 2. 数据库最终基线（21 张规范表，全表全字段中文注释）

通过 SQLAlchemy 2.x + Alembic 统一管理（迁移脚本：`backend/migrations/versions/20261031_0001_phase1_core_foundation.py`），共建立 **21 张规范表**，所有表与字段均包含非空中文注释，并在迁移时同步写入 `table_column_comments` 元数据字典表：

| 序号 | 表名 | 领域归属 | 中文说明 |
| :--- | :--- | :--- | :--- |
| 1 | `projects` | 项目与权限 | 项目主表（工作区路径、默认分支、归档状态） |
| 2 | `mcp_project_tokens` | 项目与权限 | 项目级 MCP 访问令牌表（`SHA-256` 哈希、权限作用域、吊销时间） |
| 3 | `tasks` | 规划与拆解 | 项目任务表（优先级、状态、当前修订号、负责人 Runtime） |
| 4 | `task_dependencies` | 规划与拆解 | 任务依赖有向无环图（DAG）边表 |
| 5 | `task_revisions` | 规划与拆解 | 任务追加式不可变修订历史表（Append-Only） |
| 6 | `steps` | 规划与拆解 | 任务执行步骤表（序号、验收标准、当前修订号） |
| 7 | `step_revisions` | 规划与拆解 | 步骤追加式不可变修订历史表（Append-Only） |
| 8 | `runtime_providers` | Runtime 注册 | Runtime 提供方能力边界表（`L0_OBSERVE`–`L3_SDK_INTERVENE`） |
| 9 | `machines` | Runtime 注册 | 宿主机/节点注册表（主机指纹、平台、心跳状态） |
| 10 | `runtime_instances` | Runtime 注册 | 运行时实例心跳与能力快照表 |
| 11 | `sessions` | 会话与远程 | 编排会话主表（关联项目/任务/步骤、支持 `waiting_interaction`、官方 Remote URL） |
| 12 | `external_session_refs` | 会话与远程 | 外部官方会话句柄映射表 |
| 13 | `executions` | 执行与重试 | 任务/步骤执行单表（目标、状态、起止时间、退出码） |
| 14 | `execution_attempts` | 执行与重试 | 执行重试记录表（Attempt 序号、触发原因、诊断摘要） |
| 15 | `decisions` | 决策与产物 | 交互式决策与风险门禁表（风险等级、选项列表、推荐项、`timeout_policy`、处理来源） |
| 16 | `artifacts` | 决策与产物 | 执行产物索引表（报告/计划/补丁元数据、校验和、大小） |
| 17 | `events` | 审计与基础设施 | 不可变领域事件与进度时间线表（MCP 回写与系统审计） |
| 18 | `device_sessions` | 审计与基础设施 | 受控设备配对会话表 |
| 19 | `push_subscriptions` | 审计与基础设施 | Web Push 订阅端点表 |
| 20 | `command_idempotency` | 审计与基础设施 | 命令与决策提交幂等性记录表（`idempotency_key`、请求指纹、响应快照） |
| 21 | `table_column_comments` | 审计与基础设施 | 全表全字段中文注释元数据字典表 |

---

## 3. 后端架构与清理清单

### 3.1 新建/扶正的一级模块
- [backend/config.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/config.py)：`AppSettings` 配置中心（SQLite 路径、端口、受控 CORS 正则、日志目录）。
- [backend/domain/](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/domain/enums.py)：领域枚举（含 `SessionStatus.WAITING_INTERACTION`、`DecisionTimeoutPolicy`）、异常体系与领域服务（DAG 环检测 `dependency_graph.py`、追加式修订 `revision_policy.py`、高风险决策门禁 `decision_policy.py`）。
- [backend/db/](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/db/engine.py)：SQLite WAL 引擎工厂、在线热备份 `backup.py`、`SqlAlchemyUnitOfWork` 与 21 张表 ORM 模型。
- [backend/security/](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/security/path_guard.py)：工作区路径沙箱守卫、项目级 MCP 令牌哈希守卫与基于 `device_sessions` 表的设备配对鉴权器 `auth_guard.py`。
- [backend/application/](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/application/decision_service.py)：8 个应用层用例服务（`ProjectService`、`PlanningService`、`RuntimeRegistryService`、`SessionService`、`ExecutionService`、`DecisionService`、`ArtifactService`、`EventService`）。
- [backend/api/](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/api/router.py)：完整 `/api/v1/*` REST 路由与 Pydantic v2 请求响应契约。
- [backend/app.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/backend/app.py) & [run.py](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/run.py)：Clean-Slate 启动入口与 `--doctor` 基础设施自检。

### 3.2 已清理的旧代码与文件
- 已删除：`backend/v2/`、`tests/v2/`、`backend/adapters/`、`backend/engines/`、`backend/routes/`
- 已删除：`backend/core/{database,schema,sqlite_snapshot,tailscale_auth,workspace_workflows}.py`
- 已删除：旧原生 ES Modules 前端文件 `frontend/app.js`、`frontend/styles.css`、`frontend/tailwind.min.js`、`frontend/js/*.js`
- 已删除：旧架构测试 `tests/test_*.py` 及过时根目录文件 `DESIGN.md`、`TODOS.md`、`changes.patch`

---

## 4. Phase 2 前后端核心能力交付

### 4.1 `/api/v1/*` REST API
- 系统与总览：`GET /api/v1/health`、`GET /api/v1/overview`
- 项目与 MCP 令牌：`GET/POST /api/v1/projects`、`GET/PATCH /api/v1/projects/{id}`、`GET/POST /api/v1/projects/{id}/mcp-tokens`、`DELETE /api/v1/projects/{id}/mcp-tokens/{token_id}`
- 任务、依赖与修订：`GET/POST /api/v1/projects/{id}/tasks`、`GET/PATCH /api/v1/tasks/{id}`、`POST /api/v1/tasks/{id}/revisions`、`POST /api/v1/projects/{id}/dependencies`
- 步骤与修订：`GET/POST /api/v1/tasks/{id}/steps`、`PATCH /api/v1/steps/{id}`、`POST /api/v1/steps/{id}/revisions`
- 会话、执行与产物：`GET/POST /api/v1/sessions`、`GET/PATCH /api/v1/sessions/{id}`、`GET/POST /api/v1/executions`、`POST /api/v1/executions/{id}/attempts`、`GET/POST /api/v1/artifacts`
- 交互式 Decision：`GET/POST /api/v1/decisions`、`POST /api/v1/decisions/{id}/resolve`（支持 `Idempotency-Key` 幂等提交）、`POST /api/v1/decisions/{id}/expire`
- Runtime 与事件时间线：`GET /api/v1/runtimes/providers`、`GET /api/v1/runtimes/machines`、`GET/POST /api/v1/runtimes/instances`、`GET/POST /api/v1/events`

### 4.2 `frontend/` React + TypeScript + Vite 与交互式 Decision
- **五大核心视图**：
  - `P01 工作总览`：全局统计卡片、紧急待办 Decision 快照、活跃编排会话与审计时间线。
  - `P04–P08 项目与任务`：项目创建、MCP Token 签发、任务/步骤拆解、DAG 依赖绑定、Append-Only 追加式修订历史。
  - `P09–P10 会话详情（v1.3 收敛版）`：展示编排上下文、MCP 回写时间线、关联 Decision/Artifact、Runtime/Machine 元信息与 **打开官方 Remote** 主入口；**不复刻原生聊天气泡与工具调用 UI**；仅当对应 Runtime 的 `L2_CONTROLLED_SEND` 经真实验证后才允许显示受控指令框。
  - `P02–P03 待决策中心`：跨项目集中处理与新决策模拟触发。
  - `P14 Runtime 中心`：展示 `L0`–`L3` 能力边界矩阵与宿主机状态。
- **全局交互式决策弹窗（[InteractiveDecisionModal.tsx](file:///d:/Development%20Files/PythonProjects/stu/AgentDeck/frontend/src/components/InteractiveDecisionModal.tsx)）**：
  - 跨页面自动唤醒；
  - 45 秒动态倒计时与进度条；用户进行按键、点击或输入自定义意见时立即暂停倒计时（`pausedByUser`）；
  - 遵循 Decision 自身的 `timeout_policy`：高风险（`high`/`critical`）及 `risk_gate` 类型严禁启用 `auto_submit_recommended`；
  - 桌面端支持 `1`–`9`、`Enter`、`Esc` 快捷键，移动端选项按钮满足 `min-height: 48px` 触控规范；
  - 提交采用固定 `Idempotency-Key` + 800ms/1600ms/3200ms 指数退避重试，失败时完整保留用户已选答案与补充说明。

---

## 5. 验证记录与未验证事项

### 5.1 实际执行的验证命令与结果
1. **全量重建 SQLite 数据库并执行 Alembic 迁移**：
   - 命令：`python -m alembic upgrade head`
   - 结果：退出码 `0`，成功创建全部 **21 张规范表** 及 `table_column_comments` 中文注释字典。
2. **系统自检探针（`--doctor`）**：
   - 命令：`python run.py --doctor`
   - 结果：退出码 `0`，`journal_mode=wal`、`foreign_keys=1`、`busy_timeout=15000`，21 张规范表就绪，4 个默认 `RuntimeProvider` 与本机 `Machine` 种子幂等初始化成功。
3. **Python 字节码与语法全量检查**：
   - 命令：`python -m compileall -q backend run.py tests`
   - 结果：退出码 `0`，无语法或导入错误。
4. **Phase 1 领域模型与 Phase 2 REST API 定向测试**：
   - 命令：`python -m pytest -q`
   - 结果：`4 passed in 4.05s`（覆盖 21 张表结构与非空中文注释校验、DAG 环检测、Append-Only 修订、高风险决策门禁、`/api/v1/*` 端到端 CRUD、Decision 幂等提交与关联 Session 自动恢复 `running`）。
5. **前端依赖安装与生产构建验证**：
   - 命令：`cd frontend; npm install --no-audit --no-fund` 与 `cd frontend; npm run build`
   - 结果：退出码 `0`，保留 `frontend/package-lock.json`；`tsc -b && vite build` 零错误通过，产出 `frontend/dist/index.html` 及静态资源包。
6. **前后端集成 HTTP 探针验证**：
   - 命令：`TestClient(create_app())` 请求 `GET /api/v1/health` 与 `GET /`
   - 结果：`status=healthy`、`canonical_table_count=21`，根路径 `/` 正常托管返回编译后的 React SPA 页面。

### 5.2 未验证事项说明
- 遵照操作红线，未调用自动化浏览器子代理（`browser_subagent`）进行 UI 自动化点击测试。
- 遵照阶段边界，Phase 4 的真实 MCP SSE/Stdio 协议传输与 Phase 6 的 Claude Code 官方 SDK 真实进程控制在各自后续阶段实施与验证。

