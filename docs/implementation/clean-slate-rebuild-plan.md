# AgentDeck 彻底重构（Clean-Slate）架构收敛与执行方案

- **更新日期**：2026-10-31
- **适用原则**：不迁就旧代码、不迁就旧数据库、不迁就旧 UI；彻底取消新旧双轨过渡，以 `v1.3` 最终架构为唯一建设目标。

---

## 1. 四大核心调整落地准则

### 1.1 数据库：全量纯净重建（废除旧 `t_*` 兼容包袱）
- **废除双轨共存**：不再保留 `t_unified_session_meta`（287 条旧记录）、`t_session_alias`、`t_tool_approval_log`、`t_schema_fingerprint`、`t_schema_drift_log`、`t_workspace_bookmark` 等旧表。
- **一次性建成 v1.3 完整规范表体系（统一由 SQLAlchemy 2.x + Alembic 管理，全表全字段带中文注释并写入 `table_column_comments`）**：
  1. `projects`（项目主表，`root_path` 白名单边界）
  2. `mcp_project_tokens`（绑定单一 `project_id` 的 MCP 最小权限令牌表）
  3. `tasks`（支持同项目父子树，`platform_status` 含 `waiting_interaction` / `waiting_decision` / `needs_revalidation`，与 `agent_reported_status` 分离）
  4. `task_dependencies`（同项目轻量有向无环 DAG 依赖表）
  5. `task_revisions`（任务追加式修订快照表）
  6. `steps`（任务步骤表，支持 `needs_revalidation`）
  7. `step_revisions`（步骤追加式修订快照表）
  8. `runtime_providers`（4 大 Agent Runtime 定义与默认 `CapabilityManifest`）
  9. `machines`（宿主机器表）
  10. `runtime_instances`（Runtime 实例、L0–L3 控制等级、可选非敏感 `ModelEndpointDescriptor`）
  11. `sessions`（强制归属 `project_id NOT NULL`，四层状态含 `waiting_interaction`）
  12. `external_session_refs`（外部原生会话映射与脱敏官方 Remote 入口）
  13. `executions`（任务/步骤执行单表，与 Session/Attempt 三层分离）
  14. `execution_attempts`（单次执行尝试记录表）
  15. `decisions`（持久化交互式决策与检查点表，吸收 `plan-interactive-decision-modal.md` 需求）
  16. `artifacts`（交付产物与关键变更登记表）
  17. `events`（与业务状态同事务原子提交的全局递增 `seq_id` 事件流表）
  18. `device_sessions` / `push_subscriptions` / `command_idempotency`（设备配对鉴权、iOS PWA Web Push 订阅、命令与决策提交幂等键表）
  19. `table_column_comments`（全表全字段中文注释字典表）

### 1.2 后端：单一正统架构（废除 `/api/v1` vs `/api/v2` 双轨）
- **统一 API 命名空间**：直接使用标准 `/api/v1/*` 暴露新架构 REST API，不再维护旧版 `/api/v1/sessions`（`UnifiedSession`）聊天接口。
- **退役违规与废弃模块**：
  - 彻底移除 `backend/adapters/`（含 Antigravity 逆向 LS Connect-RPC、OpenCode 内部 SQLite 直读、Claude 私有 JSONL 扫描器及 Schema 漂移守卫）；
  - 彻底移除 `backend/engines/` 中围绕旧 `UnifiedSession` 的内存态 `approval.py`、`session_aggregator.py`、`watcher.py`、`stream_runner.py`、`model_catalog.py`、`cache.py`；
  - 彻底移除旧 `backend/routes/` 與 `backend/core/database.py`、`backend/core/schema.py`、`backend/core/sqlite_snapshot.py`。
- **保留并整合的高价值通用基础设施**：
  - `backend/core/logger.py`（Windows `SafeRotatingFileHandler` + 三段式中文异常汇聚 `logs/error.log`）
  - `backend/core/network.py`、`qr_box.py`、`awake_lock.py`、`push_service.py`（Tailscale/ZeroTier/LAN 探测、终端配对二维码、Windows 防休眠锁、原生 VAPID Web Push）
  - `backend/core/workspace_git.py`（受 `Project.root_path` 白名单保护的只读 Git 状态与 Diff 提取）

### 1.3 前端：直接在 `frontend/` 构建 React + TypeScript + Vite
- **不再建立双套前端目录**：无需保留旧原生 ES Modules 代码，直接清理 `frontend/js/*.js`、`frontend/app.js`、`frontend/tailwind.min.js`，仅保留 `frontend/` 下的 PWA 图标资产（`apple-touch-icon.png`、`icon-192.png`、`icon-512.png`、`icon-1024.png`、`favicon.ico`）与 Web Push Service Worker 逻辑，在 `frontend/` 原地初始化 `React + TypeScript + Vite + Tailwind CSS + TanStack Query + Zustand + Lucide React` 工程。

### 1.4 交互式决策弹窗（吸收 `plan-interactive-decision-modal.md` 产品精华，重塑 v1.3 合规底座）
- **保留并强化的产品与领域能力**：
  1. **状态显式化**：`Task`、`Session`、`Execution` 状态字典正式纳入 `waiting_interaction`（消除假 `running` 静默卡死）。
  2. **结构化 `Decision` 模型**：支持多题/单选/多选/补充文本、选项推荐标记（`is_recommended`）、倒计时配置（如默认 `timeout_sec = 45`）、超时策略（`auto_submit_recommended` / `manual_only`）与过程留痕（区分 `resolved_by = "user_manual" | "timeout_auto_recommended"`）。
  3. **幂等与容错提交管道**：后端决策应答接口支持 `Idempotency-Key` 且对已处理完成的 `Decision` 重复提交相同/幂等请求时兼容返回幂等成功态（或明确 `409 AlreadySettled` 结构化状态供前端直接视为已落定），前端实现 3 次指数退避重试（`1s / 2s / 4s`），失败保留用户已选答案。
  4. **跨页面全局模态框与双端交互**：全局置顶唤醒、桌面端数字键 `1/2/3` 快捷选定 + `Enter` 提交、移动端 ≥48px 单手触控热区、45s 动态微进度条（用户一旦点击或输入立即 `paused` 挂起倒计时）。
- **彻底重塑的底层接入边界（严守 v1.3 铁律）**：
  - **废除**旧方案中“通过事件总线向 Antigravity / Claude Code 逆向注入 Prompt”的做法；
  - **通道 1（通用标准 MCP 协同，Phase 4）**：任何支持 MCP 的 Agent（Claude Code / Codex / OpenCode / Antigravity）通过调用 AgentDeck MCP 工具 `checkpoint_request` 挂起并登记 `Decision`，使所属 Task/Session 进入 `waiting_interaction`，并通过 `decision_get` 获取用户拍板或 45s 自动采纳的推荐结果；
  - **通道 2（官方 L3 接口直连，Phase 6/7 验证后按需开启）**：仅当特定 Runtime（如 OpenCode 官方 HTTP Form/Permission API、Codex App Server Approval）在 Phase 6 通过真实验证后，才通过官方 L3 Connector 双向同步挂起表单与应答。

---

## 2. 数据库重建与旧文件清理操作范围声明（执行前确认清单）

> **安全边界承诺**：以下所有删除与重建操作 **100% 严格限制在当前项目工作区目录 `d:\Development Files\PythonProjects\stu\AgentDeck` 内部**，绝不触碰项目目录之外的任何系统文件或外部 Agent 本地目录。

### 2.1 数据库文件重建范围
- 目标文件（仅限项目内 `data/` 目录）：
  - `data/agentdeck.db`、`data/agentdeck.db-wal`、`data/agentdeck.db-shm`
  - `data/metadata.db`（0 字节历史遗留空文件）
- 保留文件：
  - `data/vapid_keys.json`（保留 VAPID 密钥对，省去重新生成公钥开销）
  - `data/backups/pre_phase1_agentdeck_20261031_220336.db`（保留此前生成的只读安全备份归档）

### 2.2 后端旧代码清理与目录扶正范围
- 拟删除的旧模块（仅限项目内 `backend/` 与 `tests/`）：
  - `backend/adapters/` 下全部旧私有协议/扫描器文件
  - `backend/engines/` 下除 `process_utils.py` 外的旧内存态引擎文件
  - `backend/routes/` 下旧版路由文件
  - `backend/core/database.py`、`backend/core/schema.py`、`backend/core/sqlite_snapshot.py`、`backend/core/workspace_workflows.py`
  - `tests/test_*.py` 下耦合旧私有协议与旧 `UnifiedSession` 的 9 个旧测试文件
- 目录扶正：
  - 将 `backend/v2/` 扶正为 `backend/` 一级正统模块（`config.py`, `db/`, `domain/`, `security/`, `application/`, `api/`），路由统一挂载于 `/api/v1/*`。

### 2.3 前端旧代码清理范围
- 拟删除的旧前端脚本（仅限项目内 `frontend/`）：
  - `frontend/js/`（34 个旧原生 JS 文件）
  - `frontend/app.js`、`frontend/tailwind.min.js`、`frontend/styles.css`、`frontend/index.html`（将由 Vite + React + TypeScript 新工程替换）
  - 根目录旧 `DESIGN.md`、`TODOS.md`、`changes.patch`
- 保留并迁入新前端 `public/` 的资产：
  - `frontend/apple-touch-icon.png`、`favicon-16x16.png`、`favicon-32x32.png`、`favicon.ico`、`icon-192.png`、`icon-512.png`、`icon-1024.png`、`manifest.json`、`sw.js`（升级适配新路由与推送唤醒）。

