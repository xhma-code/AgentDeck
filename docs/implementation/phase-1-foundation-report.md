# AgentDeck Phase 1：基础工程、核心领域模型与安全加固交付报告

- **当前开发主线**：`04_AgentDeck_Architecture_and_Roadmap_v1.3.md`（Phase 0–11 唯一主线）
- **开发分支**：`feat/project-task-foundation`（基线 HEAD：`0711fbf`）
- **交付日期**：2026-10-31

---

## 1. Gate 0 架构冻结与 Phase 1 开始前检查记录

### 1.1 已冻结核心决策
1. **阶段路线**：以 `v1.3` Phase 0–11 为唯一主线；Phase 1 聚焦核心领域模型、SQLAlchemy 2.x、Alembic 与 SQLite 基础设施；Phase 4 建设 MCP Server；Phase 6 执行 Runtime 官方接口与 Claude Code 国内兼容模型环境 SDK Spike；Phase 9 按需可选建设 Worker JSONL。
2. **P10 页面边界**：收敛为“编排上下文 + MCP 回写时间线 + Decision/Artifact + Runtime/Machine/Session 信息 + 打开官方 Remote”，仅在 Runtime L2 能力真实验证通过后显示受控指令框，不再复刻原生聊天与工具审批 UI。
3. **前端过渡**：采用方案 A，在后续阶段新建 `frontend-v2/`（React + TypeScript + Vite）并行开发，保留现有 `frontend/` 直至新前端 MVP 闭环验收后再正式切换归档。
4. **安全优先**：本阶段同步落地 `WorkspaceDocumentReader` 的 `Project.root_path` 白名单与 symlink/junction 逃逸拦截、MCP Project-scoped Token 独立鉴权守卫基线、以及受控 CORS 收敛。

### 1.2 前置安全检查与在线热备结果
- **分支核验**：当前分支 `feat/project-task-foundation`（非 `main` 分支），工作区暂存项 `M start.cmd` 保持原样未触碰。
- **SQLite Backup API 在线备份**：
  - 备份路径：`data/backups/pre_phase1_agentdeck_20261031_220336.db`
  - 完整性校验：`PRAGMA integrity_check = ok`
  - 备份前存量基线：`t_unified_session_meta = 287`、`t_device_session = 37`、`t_push_subscription = 1`、`t_workspace_bookmark = 2`。

---

## 2. Phase 1 阶段方案说明（目标 / 边界 / 回滚）

- **阶段目标**：建立不依赖任何外部 Agent Runtime 即可独立运作的 Project / Task / Step / Dependency / Revision / Session / Event / Runtime 最小登记核心领域底座、SQLAlchemy 2.x + Alembic 迁移基线、SQLite 在线热备能力及三大高优安全加固。
- **本阶段明确不做事项**：
  - 不接入外部 Runtime CLI/SDK，不触碰 CCSwitch；
  - 不实现 MCP Server 工具路由（Phase 4）；
  - 不提前建设通用 Worker JSONL（Phase 9 按需）；
  - 不提前破坏或删除旧 `frontend/` 及旧 `/api/v1/*` 会话接口；
  - 不主动执行旧版全量测试套件，不主动提交 Git 暂存区。
- **风险与回滚方案**：
  - 迁移异常回滚：执行 `python -m alembic downgrade -1` 可按外键逆序无损移除全部 13 张新增表，或直接用 `data/backups/pre_phase1_agentdeck_20261031_220336.db` 秒级恢复。
  - 路由隔离：新架构接口全部挂载于 `/api/v2/*`，与存量 `/api/v1/*` 零冲突。

---

## 3. 修改与新增文件清单

### 3.1 修改文件（4 个）
1. `pyproject.toml`：补充声明本机已安装的 `pydantic-settings>=2.0.0`、`sqlalchemy>=2.0.0`、`alembic>=1.14.0`（未执行联网装包）。
2. `backend/app.py`：收敛 CORS（移除 `allow_origins=["*"]` + `credentials=True` 风险组合，改用受控专网/局域网/本机正则白名单），挂载 `v2` SQLAlchemy 引擎、`DomainError` 统一异常处理与 `/api/v2` 路由。
3. `backend/core/workspace.py`：加固 `WorkspaceDocumentReader.read_document`，强制通过 `resolve_and_verify_path_within_roots` 执行受控根目录白名单与 symlink / Windows Junction 逃逸拦截。
4. `backend/routes/workspaces.py`：在 `/api/v1/files/read` 中动态汇总活跃 `projects.root_path` 与未隐藏工作区路径作为强制白名单传入文件读取器。

### 3.2 新增文件（24 个）
- **配置与数据库基础设施**：
  - `alembic.ini`（纯 ASCII 编码，规避 Windows 中文环境 `cp936` 解析异常）
  - `backend/migrations/env.py`、`backend/migrations/script.py.mako`
  - `backend/migrations/versions/20261031_0001_phase1_core_foundation.py`
  - `backend/v2/__init__.py`、`backend/v2/config.py`
  - `backend/v2/db/__init__.py`、`backend/v2/db/base.py`、`backend/v2/db/engine.py`、`backend/v2/db/backup.py`、`backend/v2/db/uow.py`
- **ORM 数据模型（全表全字段中文注释）**：
  - `backend/v2/db/models/__init__.py`、`project.py`、`planning.py`、`runtime.py`、`session.py`、`event.py`
- **纯领域层与安全边界层**：
  - `backend/v2/domain/__init__.py`、`enums.py`、`exceptions.py`、`types.py`
  - `backend/v2/domain/services/__init__.py`、`dependency_graph.py`、`revision_policy.py`
  - `backend/v2/security/__init__.py`、`path_guard.py`、`mcp_token_guard.py`
- **应用层服务与 `/api/v2` 基线路由**：
  - `backend/v2/application/__init__.py`、`project_service.py`、`planning_service.py`、`runtime_registry_service.py`、`session_service.py`
  - `backend/v2/api/__init__.py`、`backend/v2/api/router.py`
- **Phase 1 定向验收测试套件**：
  - `tests/v2/__init__.py`、`tests/v2/test_phase1_foundation.py`

---

## 4. 数据库迁移、定向验证与 Windows 启动结果

### 4.1 数据库迁移结果
- 执行命令：`python -m alembic upgrade head` 及 `python -m alembic current`
- 当前迁移版本：`20261031_0001 (head)`
- 新增规范表（共 13 张）：`projects`、`mcp_project_tokens`、`tasks`、`task_dependencies`、`task_revisions`、`steps`、`step_revisions`、`runtime_providers`、`machines`、`runtime_instances`、`sessions`、`external_session_refs`、`events`。
- 数据完整性与中文注释核验：
  - `PRAGMA integrity_check`：`ok`
  - 存量表零损毁：`t_unified_session_meta = 287`、`t_device_session = 37`、`t_push_subscription = 1`、`t_workspace_bookmark = 2`
  - 初始种子：`runtime_providers = 4`（未验证能力诚实标为 `unverified`）、`machines = 1`（`local-machine`）
  - 中文注释字典：`t_table_column_comments` 总记录数由 `64` 增至 `209`（新增 13 张表共 145 个字段中文注释全部入库）。

### 4.2 定向验证命令与结果
- **运行环境**：Windows (`win32`), Python `3.12.10`, `pytest 8.3.3`, 工作目录 `d:\Development Files\PythonProjects\stu\AgentDeck`（含空格路径）。
- **验证命令 1（静态语法编译）**：`python -m compileall -q backend run.py tests/v2` → **退出码 `0`**。
- **验证命令 2（Phase 1 新增模块与安全检查）**：`python -m pytest tests/v2/test_phase1_foundation.py -v` → **`3 passed in 2.31s`（0 warnings）**，完整验证：
  - 含空格路径下 SQLite Backup API + Alembic `upgrade -> downgrade -> upgrade` 往返；
  - `SqlAlchemyUnitOfWork` 业务变更与 `events` 写入同事务原子提交及外键异常时 100% 整体回滚；
  - 无 Project 创建 Session 拦截、跨 Project 父子/依赖/会话关联拦截、DAG 环路检测；
  - Step / Task 追加式 Revision 历史保留及下游自动进入 `needs_revalidation`（不自动重跑）；
  - Agent 报完成不等于平台验收完成、Remote 断开不误判失败；
  - `WorkspaceDocumentReader` 白名单拦截与 Windows `mklink /J` Junction 越界逃逸阻断；
  - MCP Token 强制绑定单一 Project、最小权限 `scope`、吊销失效及无本机免密；
  - 恶意外部 Origin 的 CORS 阻断与本机/Tailscale 受控 Origin 放行。
- **验证命令 3（Windows 真实 Uvicorn 启动探针）**：启动 `uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 18765`：
  - `GET /api/v2/health` → `200 OK`（`journal_mode=wal`, `foreign_keys=1`, `busy_timeout=15000`, `schema_ready=True`, `present_table_count=13`）；
  - `GET /api/v1/health` → `200 OK`（`healthy`，存量入口兼容无损）。
- **本阶段未验证事项说明**：
  - 按授权边界，未运行旧版 `tests/test_*.py` 全量回归套件；
  - 前端 UI 与完整项目管理 REST 端点将在 Phase 2 建设并验证。

---

## 5. 下一阶段（Phase 2：REST API 与项目管理 UI）开始前计划（供验收后审阅）

- **Phase 2 目标**：
  1. 在 `/api/v2/*` 下暴露完整的 Project / Task / Step / Dependency / Revision、Session 登记查询、Runtime / Machine / RuntimeInstance 登记查询及 Event 列表 REST API（含 Pydantic 请求响应契约与乐观锁/幂等处理）；
  2. 初始化 `frontend-v2/`（React + TypeScript + Vite + Tailwind CSS + TanStack Query + Zustand + Lucide React），按 UI 规格书（`#101318` 深灰蓝黑设计语言）落地核心项目管理闭环页面：配对/鉴权承接（P00）、全局驾驶舱首页（P01）、项目列表与新建导入（P04/P05）、项目概览（P06）、任务树（P07）、任务详情与追加式 Revision 编辑（P08）、项目 Session 列表与收敛版 P10 详情页（P09/P10），适配移动端与桌面端。
- **Phase 2 不做事项**：
  - 不开发 MCP Server（Phase 4）；
  - 不开发 Decision 收件箱与 Execution/Attempt 深度持久化流转（Phase 5）；
  - 不接入外部 Agent SDK/CLI 控制（Phase 6/7）；
  - 不删除或覆盖现有 `frontend/`。
- **预计新增/修改文件**：
  - 后端：新增 `backend/v2/api/schemas.py`、`projects.py`、`tasks.py`、`sessions.py`、`runtimes.py`、`events.py` 并汇总挂载至 `backend/v2/api/router.py`；
  - 前端：新建 `frontend-v2/` 工程目录及路由/页面/API 客户端；
  - 测试：新增 `tests/v2/test_phase2_api.py`。
- **数据库/API 变化**：Phase 1 核心表已完备，Phase 2 预计无需破坏性表结构改动，主要新增 `/api/v2/projects`、`/api/v2/projects/{id}/tasks`、`/api/v2/runtimes` 等 REST 端点。
- **注意（前端依赖说明）**：初始化 `frontend-v2/` 时将需要 Node 依赖（`react`, `vite`, `typescript`, `@tanstack/react-query`, `zustand`, `lucide-react`, `tailwindcss` 等），届时需确认是否允许在 `frontend-v2/` 下执行 `npm install`。

