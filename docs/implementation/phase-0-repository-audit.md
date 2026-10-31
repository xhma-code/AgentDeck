# AgentDeck Phase 0：仓库复核与架构冻结审查报告

- **文档版本**：v1.0
- **审查日期**：2026-10-31
- **当前阶段**：Phase 0（只读复核与架构冻结，零业务代码修改、零数据库迁移、零依赖变更）
- **设计基线**：`docs/agentdeck-handoff/` 下 `00`–`05` 六份正式交接文档（以 `04_AgentDeck_Architecture_and_Roadmap_v1.3.md` 与 `05_AgentDeck_Official_Agent_Integration_Research_2026-10-31.md` 为最新架构收敛口径）

---

## 1. 仓库事实摘要与环境基线

### 1.1 Git 与工作区状态

| 检查项 | 实测结果 | 风险与合规说明 |
| :--- | :--- | :--- |
| 工作目录 | `d:\Development Files\PythonProjects\stu\AgentDeck` | 路径天然包含空格（`Development Files`），天然构成 Windows 空格路径测试用例 |
| 当前分支 | `feat/project-task-foundation` | 跟踪远端 `AgentDeck/feat/project-task-foundation`（`https://github.com/xhma-code/AgentDeck.git`），非 `main` 分支，符合分支隔离要求 |
| 当前 HEAD | `0711fbf` (`readme`) | 前序提交：`1615468 启动问题`、`caa17af 细节优化`、`eedcb2f iPhone Web Push`、`3490aee 会话真相状态机` |
| 暂存区（Staged） | `M  start.cmd` | 将 `python run.py --reload %*` 调整为 `python run.py %*`；受暂存区保护红线约束，本轮保持原样未触碰 |
| 未跟踪文件 | `?? docs/agentdeck-handoff/` | 6 份重构设计基线文档 |
| 历史遗留杂项 | `changes.patch` (10,808 B)、`AgentDeck.lnk`、`data/metadata.db` (0 B) | 旧开发周期遗留物，建议在后续清理清单中经确认后归档或移除 |

### 1.2 本机运行环境与依赖盘点

- **操作系统**：Windows（PowerShell 环境）
- **Python**：`3.12.10`（语法静态编译 `python -m compileall -q backend run.py` 退出码 `0`）
- **Node.js / npm**：`v24.19.0` / `11.12.0`（本机已具备现代前端构建环境，但仓库内尚无 `package.json`）
- **已安装关键 Python 包**：
  - Web 与异步：`fastapi 0.115.0`、`uvicorn 0.30.6`、`anyio 4.13.0`、`httpx 0.27.2`、`watchfiles 1.1.1`
  - 数据与校验：`pydantic 2.9.2`、`pydantic-settings 2.7.1`、`SQLAlchemy 2.0.35`、`alembic 1.14.1`、`aiosqlite 0.20.0`
  - 系统与推送：`psutil 6.1.0`、`pywebpush 2.5.0`、`py-vapid 1.9.6`、`qrcode 8.2`
  - 质量工具：`pytest 8.3.3`、`pytest-asyncio 0.24.0`、`ruff 0.8.6`
- **尚未安装的依赖**：
  - `pyproject.toml` 当前未声明 `sqlalchemy`、`alembic`、`pydantic-settings`（尽管全局/当前环境已安装，仍需在重构时正式锁入 `pyproject.toml`）；
  - 官方 Python MCP SDK（`mcp`）**未安装**；
  - Claude Agent SDK（`claude-agent-sdk`）、OpenAI Codex SDK、Antigravity SDK 等**均未安装**。

### 1.3 仓库现有目录结构全景

```text
AgentDeck/
├── backend/
│   ├── app.py                         # FastAPI 入口、生命周期、中间件与异常拦截
│   ├── adapters/                      # 各 Agent 适配器（重度耦合本地私有存储与逆向 RPC）
│   │   ├── antigravity_{adapter,fork,ls_bridge,model_resolver,parser,scanner,tail_inspector}.py
│   │   ├── claude_{adapter,parser,scanner}.py
│   │   ├── opencode_{adapter,bridge,parser,scanner}.py
│   │   ├── base.py, models.py, schema_guard.py, tool_utils.py
│   │   └── schemas/                   # JSONL / SQLite 漂移指纹规则
│   ├── core/                          # 基础设施组件
│   │   ├── database.py                # 原生 sqlite3 封装 + 10 张 t_* 表手写 DDL
│   │   ├── logger.py                  # SafeRotatingFileHandler + 结构化中文错误日志
│   │   ├── network.py, tailscale_auth.py, qr_box.py, awake_lock.py
│   │   ├── push_service.py            # 原生 VAPID Web Push 服务
│   │   ├── sqlite_snapshot.py         # 外部 Agent SQLite 快照复制读取
│   │   └── workspace.py, workspace_git.py, workspace_workflows.py, schema.py
│   ├── engines/                       # 运行时内存态引擎
│   │   ├── approval.py                # 内存态工具审批协调器（无持久化恢复）
│   │   ├── command_bus.py             # 会话串行锁 + SQLite 幂等记录
│   │   ├── event_bus.py               # 纯内存 deque(200) 事件总线 + WebSocket 广播
│   │   ├── process_pool.py, process_utils.py, stream_runner.py
│   │   └── session_aggregator.py, watcher.py, cache.py, model_catalog.py
│   └── routes/                        # HTTP / WebSocket 路由
│       └── auth.py, deps.py, push.py, schemas.py, sessions.py, static_pwa.py, system.py, websocket.py, workspaces.py
├── frontend/                          # 零构建原生 ES Modules 单页聊天台（无 package.json / React / TS）
│   ├── index.html, app.js, styles.css, tailwind.min.js, manifest.json, sw.js
│   └── js/                            # 34 个模块化原生 JS 文件（状态、渲染、消息、审批、漫游等）
├── data/                              # agentdeck.db (192KB, WAL)、vapid_keys.json、metadata.db (0B)
├── logs/                              # app.log、error.log
├── tests/                             # 9 个 pytest 测试文件
├── docs/                              # 历史计划文档 + agentdeck-handoff/ 6 份正式基线
└── run.py, start.cmd, stop.cmd, pyproject.toml, DESIGN.md, README.md, TODOS.md
```

---

## 2. 第一性原理审视：6 份基线文档的演进冲突与对齐准则

在完整精读 `00`–`05` 六份文档与仓库现状后，发现基线文档之间存在 **从 v1.1（`01`/`02`/`03`）向 v1.3（`04`/`05`）演进产生的 5 处关键设计张力**。在冻结架构前必须明确指出并统一口径：

### 2.1 冲突 1：Runtime 接入主线与 Worker JSONL 的地位变化
- **差异表现**：
  - `01`（架构评审 v1.1）与 `03`（分阶段计划 v1.0）采用“外部 Agent MCP + 平台托管 Worker JSONL IPC 双主线并行”，`03` 将 Worker Supervisor JSONL IPC 列为 **Phase 1 必做验证项**。
  - `04`（架构路线图 v1.3）与 `05`（官方集成调研）引入 **L0 Remote Link → L1 Registry & Status → L2 Programmatic Control → L3 Approval & Recovery** 四级能力模型，确立 **“官方功能优先、先轻量接入（Remote + MCP）、不重造手机 Agent 聊天器”**，并将 **Worker Supervisor 降级为 Phase 9 按需可选模块**（若目标 Runtime 已具备稳定 HTTP Server 或 SDK 会话控制，不强行包一层通用 Worker）。
- **冻结建议**：以 `04` v1.3 与 `05` 为准。核心领域层只依赖抽象的 `RuntimeAdapter` / `CapabilityManifest`（L0–L3），**不在 Phase 1 提前建设重型通用 Worker Supervisor**；仅保留 JSONL IPC 协议规范备查，待 Phase 6 Runtime Spike 证明某适配器确实缺乏常驻控制面时再按需启用。

### 2.2 冲突 2：P10 Session 详情页的定位（自建聊天流 vs 官方 Remote 跳转）
- **差异表现**：
  - `02`（UI 原型规格书 v1.0）第 4.11 节将 P10 设计为“中间对话流 + 底部输入框发送消息”的类聊天界面。
  - `04` v1.3 第 1.3 节与第 12 节明确否定“重造手机 Agent Chat / Terminal / 工具级审批面板”，规定 AgentDeck 负责看进度、做业务级 Decision、看关键变更与打开官方 Remote（如 `claude remote-control`、OpenCode Web/App），仅在适配器真实通过 L2/L3 验证时才开放受控指令动作。
- **冻结建议**：P10 统一收敛为 **“Session 编排上下文与协同面板”**——核心展示所属 Project/Task/Step、关联 Execution/Attempt 状态、MCP 回写记录、产出的 Decision/Artifact、能力等级徽标（L0–L3）以及醒目的 **“打开官方 Remote”** 入口；仅当 `CapabilityManifest` 声明支持 `can_send_prompt=true`（L2+）时，渐进增强显示受控任务指令输入栏，彻底剥离逐条工具调用卡片、思维链折叠与底层 Terminal 输出。

### 2.3 冲突 3：阶段路线图编号（`03` 14 阶段 vs `04` 12 阶段）与 Phase 1 范围
- **差异表现**：
  - `03` 划分为 Phase 0–13：Phase 1 专做纯技术 Spike（Windows 环境、SQLite WAL/Backup、Worker JSONL、MCP 最小连通），Phase 2 才建领域模型与 Alembic 迁移。
  - `04` v1.3 划分为 Phase 0–11：**Phase 1 直接建设基础工程与核心领域模型**（Project / Task / Step / Revision / Session / Event + RuntimeProvider / Machine / RuntimeInstance 最小登记 + Alembic 迁移基线），MCP 移至 Phase 4，Runtime Spike（含 Claude Code + CCSwitch 国内模型验证）移至 Phase 6，Worker 移至 Phase 9（可选）。
- **冻结建议**：采用 **以 `04` v1.3 Phase 0–11 为主骨架、在 Phase 1 开头内嵌最小工程验证（SQLite WAL + `sqlite3.Connection.backup()` + Alembic 迁移基线）** 的融合方案（详见第 10、11 节，供 Gate 0 最终拍板）。

### 2.4 冲突 4：视觉背景色语义 Token（`DESIGN.md` 纯黑 vs `02` 深灰蓝黑）
- **差异表现**：仓库旧 `DESIGN.md` 与 `frontend/styles.css` 采用 AMOLED 纯黑 `#000000` (`--bg-canvas`)；而 `02` UI 规格书第 2.1 节明确要求 **“不使用大面积纯黑背景”**，规定 `--bg: #101318`、`--bg-elevated: #171C23`、`--primary: #8BA9FF`。
- **冻结建议**：严格遵循 `00` 优先级规则，新前端全面采用 `02` UI 规格书定义的深灰蓝黑设计 Token，旧 `DESIGN.md` 归档退役。

### 2.5 冲突 5：Model Provider 与 CCSwitch 的边界修订
- **差异表现**：`01` 第 11.1 节曾规划 `model_providers` / `model_profiles` 核心表；`04` v1.3 第 5.4 与第 7 节修正为 **AgentDeck 不做模型供应商配置管理中心，绝不集成、不读取、不修改 CCSwitch 配置与 API Key**。
- **冻结建议**：不建 `model_providers` 控制表；将 Agent 产品抽象为 `RuntimeProvider`（`claude_code` / `codex` / `opencode` / `antigravity`），仅在 `RuntimeInstance` 或 `Execution` 上保留可选、非敏感的说明性元数据 `ModelEndpointDescriptor`（如 `endpoint_mode`、`model_hint`）用于排障展示与能力降级提示。

---

## 3. 当前仓库真实代码与目标架构差异审查

### 3.1 产品与领域层本质差异
当前仓库（v2.0/v2.1）本质上是 **“本地多 Agent 会话镜像聚合器 + 远程聊天遥控器”**，围绕单一的 `UnifiedSession` 展开；而目标架构是 **“Project-first、Task-driven、Mobile-first 的个人多项目 AI 研发异步编排驾驶舱”**。
- **完全缺失的核心聚合与实体**：`Project`（当前仅有收藏夹 `t_workspace_bookmark`）、`Task`（含父子树与可选轻量依赖 `TaskDependency`）、`Step`、`TaskRevision`、`StepRevision`、`Execution`、`ExecutionAttempt`、业务级持久化 `Decision`、`Artifact`、同事务持久化 `Event`、`RuntimeProvider`、`Machine`、`RuntimeInstance`、`CapabilityManifest`、`MCP Project Token`。
- **信息噪音越界**：当前前后端花费大量代码解析并渲染 Agent 内部思维链（Thinking blocks）、逐次工具调用（Tool Use / Result）、底层文件 Diff 流与 Token 消耗账单，与新架构“低噪音驾驶舱、默认隐藏思维链与 Token、不重造 Agent IDE”的定位直接相悖。

### 3.2 后端工程与数据访问差异
- **ORM 与迁移缺失**：`pyproject.toml` 未纳入 `SQLAlchemy` 与 `Alembic`。`backend/core/database.py` 全靠原生 `sqlite3` 手写 SQL，表结构变更依赖 `_init_db()` 中逐列捕获异常的 `ALTER TABLE` 补丁，无法支撑未来 15+ 张核心领域表的严谨演进与回滚。
- **事务与事件割裂**：当前业务写入散落在各 `routes/*.py` 与 `engines/*.py` 的独立 `sqlite3.connect()` 短连接中；`backend/engines/event_bus.py`（`RealtimeEventBus`）仅维护容量为 200 的内存 `deque`，无数据库落盘，服务重启后事件序号归零，手机端断线超过缓冲区即丢失状态变迁。
- **目录分层耦合**：按 `adapters / core / engines / routes` 技术横切分包，缺少 `domain`（纯业务规则与状态机）与 `application`（用例编排与同事务 Unit of Work）边界。

### 3.3 Runtime 集成违规现状深度剖析
对照 `04` v1.3 与 `05` 铁律，现有 `backend/adapters/` 存在严重违规，**严禁原样带入新架构核心层**：
1. **Antigravity 适配器（高危违规，必须退役剥离）**：
   - `backend/adapters/antigravity_ls_bridge.py`：通过进程扫描读取 `language_server` 命令行窃取 `--csrf_token`，探测本地端口并构造逆向 Connect-RPC 帧（`GetAllCascadeTrajectories`、`SendUserCascadeMessage`、`StartCascade`），甚至硬编码枚举字节探测未知 Protobuf 字段；
   - `backend/adapters/antigravity_scanner.py` / `antigravity_fork.py`：直接扫描并二进制克隆 `~/.gemini/antigravity` 内部 `.pb` 轨迹与状态库。
   - **架构裁定**：完全违反“禁止私有 Language Server RPC、禁止二进制状态克隆进入核心”红线。新架构下 Antigravity 初期仅支持 **MCP 外部协同（L0/L1）**，待官方 CLI/SDK 成熟后再评估升级。
2. **OpenCode 适配器（半违规，需去私有库化）**：
   - `backend/adapters/opencode_adapter.py` / `opencode_scanner.py` + `sqlite_snapshot.py`：直接拷贝并 SQL 查询 `~/.local/share/opencode/opencode.db` 内部表（`session` / `message` / `part`）；
   - `backend/adapters/opencode_bridge.py`：通过 `opencode serve` 暴露的 HTTP 接口交互。
   - **架构裁定**：直读 `opencode.db` 违反“不读写 Runtime 内部私有 SQLite”原则，必须废弃；HTTP Server / SDK + MCP + Web UI Remote 方向完全契合 `05` 调研结论，可重构为首个高能力（L0→L2）Runtime 适配器。
3. **Claude Code 适配器（需转型）**：
   - `backend/adapters/claude_adapter.py` / `claude_scanner.py` / `claude_parser.py`：直接遍历解析 `~/.claude/projects/**/*.jsonl` 提取对话、思维链与 Token，通过 CLI 子进程拼接参数收发消息。
   - **架构裁定**：剥离内部 JSONL 解析器；转向 **`claude remote-control`（L0 官方远程入口）+ AgentDeck MCP（L1 结构化回写）**，并在 Phase 6 专项验证 **Claude Agent SDK 在本地配置 CCSwitch 指向国内 Anthropic 兼容模型时的真实兼容性**，验证通过前绝不承诺 L2/L3。
4. **Codex 适配器（空白）**：仓库内尚无任何 Codex 代码，后续按 `05` 调研以 App Server / SDK + MCP 方式全新接入。

### 3.4 12 项核心架构不变量现状核查表

| # | 核心架构不变量（源自 `01` / `04` v1.3） | 仓库现状 | 核查结论 |
| :-: | :--- | :--- | :---: |
| 1 | **任何 Session 必须归属明确 Project**（禁止无 Project 游离会话） | 无 `projects` 表；`t_unified_session_meta` 中 **287 条记录有 224 条 `project_path` 为空**；前端支持无目录临时会话 | **未满足** |
| 2 | **Session / Execution / ExecutionAttempt 三层严格分离** | 仅单表 `t_unified_session_meta` 混放会话与连接态，运行轮次仅存内存 `TurnContext` | **未满足** |
| 3 | **Task / Step 修订采用追加式 Revision，修订后下游自动标 `needs_revalidation`** | 完全无 Task / Step / Revision 模型与失效传播机制 | **未满足** |
| 4 | **Task 主结构保持父子树，仅允许同 Project 轻量无环 `TaskDependency`** | 完全无 Task 树与依赖校验逻辑 | **未满足** |
| 5 | **Agent 自报状态（`reported_state`）与平台判定状态（`platform_state`）严格分离** | 仅维护连接可用性 `lifecycle_status` (`creating/live/degraded/dead`)，未区分外部上报与平台采信 | **未满足** |
| 6 | **Decision 必须持久化，服务重启后可恢复待决策态，禁止仅驻留内存** | `ApprovalCoordinator` 将挂起审批保存在内存字典与 `asyncio.Future` 中，重启即丢（`t_tool_approval_log` 仅记事后日志且当前为 0 行） | **未满足** |
| 7 | **业务状态变更与 `events` 表写入必须在同一 SQLite 事务内原子提交** | `RealtimeEventBus` 纯内存环形队列（容量 200），无 `events` 表 | **未满足** |
| 8 | **SQLite 启用 WAL、`busy_timeout`、外键约束及在线热备机制** | 已开启 `PRAGMA journal_mode=WAL` 与 `timeout=15.0`，但**未开启 `PRAGMA foreign_keys=ON`**，无基于 `backup()` 的自身库热备 | **部分满足** |
| 9 | **Runtime 能力按 L0–L3 `CapabilityManifest` 诚实声明，UI 按声明裁剪入口** | 硬编码按 `agent_name` 分支判断，无统一能力清单模型 | **未满足** |
| 10 | **MCP Token 必须绑定单一 `project_id` 作用域，严禁跨 Project 越权读写** | 尚无 MCP Server 与 Project-scoped Token 机制 | **未满足** |
| 11 | **不集成/不读写 CCSwitch，不托管各 Runtime 供应商 API Key** | 现有 `model_catalog.py` 仅扫描模型列表未改写 CCSwitch，但适配器混杂底层模型切换逻辑 | **部分满足** |
| 12 | **工作区文件与 Git 访问必须严格限制在已注册 `Project.root_path` 白名单内** | `WorkspaceDocumentReader.read_document` 仅做黑名单后缀过滤，**未校验路径归属已注册 Project 根目录，亦无 symlink 逃逸拦截** | **未满足（高危）** |

### 3.5 安全边界审计发现
1. **CORS 配置过宽**：`backend/app.py` 配置了 `allow_origins=["*"]` 且 `allow_credentials=True`，重构时应收敛为同源与受控 VPN/LAN Origin 白名单。
2. **任意路径读取隐患**：`backend/core/workspace.py` 允许传入任意本机路径读取 Markdown/JSON/YAML 等文档（仅拦截 `.env`/`.pem` 等黑名单），必须改为通过 `project_id` 解析 `root_path` 并校验 `Path.resolve()` 不越界（含符号链接穿透检查）。
3. **本机 `127.0.0.1` 免密直通隐患**：`backend/routes/deps.py` 对 `127.0.0.1`/`::1` 请求无条件放行。在引入本地 MCP Server（`/mcp`）后，若沿用该策略将导致本机任何进程可免 Project Token 越权修改任意项目状态——**MCP 路由必须独立强制校验 Project Token，绝不可复用 `127.0.0.1` 免密逻辑**。
4. **URL Query 暴露长期设备 Token**：`frontend/js/api.js` 为兼容 iOS 添加主屏幕，将长期 `agd_...` Token 拼入浏览器地址栏 `?token=`，存在录屏/截屏/历史记录泄露风险，建议改为配对置换 HttpOnly SameSite Cookie + 受控存储双保险，不在常态 URL 保留明文长期凭证。

---

## 4. 领域模型与 SQLite 数据库调整方案

### 4.1 存量数据库（`data/agentdeck.db`）只读盘点
当前数据库大小 `192,512 Bytes`，`journal_mode = wal`，共包含 **10 张表**：

| 现有表名 | 存量行数 | 现状用途与关键数据特征 | 重构处置策略 |
| :--- | ---: | :--- | :--- |
| `t_unified_session_meta` | **287** | 统一会话元数据（`agy_ide` 125、`opencode` 74、`agy_cli` 74、`claude_code` 13、`antigravity` 1）；**其中 224 条 `project_path` 为空/NULL**，仅 63 条带路径 | **冻结为历史旧表**；有路径记录可按需关联合并至对应 `Project`，无路径 224 条绝不强塞进新 `sessions` 表破坏外键不变量，保留旧表只读归档备查 |
| `t_device_session` | **37** | 已配对移动/桌面设备及 `token_hash` (SHA-256) | **平滑迁移复用**至新设备认证表，免除用户存量手机重新扫码 |
| `t_push_subscription` | **1** | 活跃 iOS PWA Web Push 订阅端点与密钥 | **平滑迁移复用**至新推送订阅表，配合现有 `data/vapid_keys.json` 保持推送不断连 |
| `t_workspace_bookmark` | **2** | 工作区收藏（`DatatoAPI` 隐藏、`AgentDeck` 显示） | **平滑导入**为首批初始化 `Project` 候选记录（校验本机路径存在性后入库） |
| `t_command_idempotency` | **19** | 客户端命令幂等键缓存 | 升级结构后重建（历史短期幂等记录无需跨代保留） |
| `t_session_alias` | **2** | 旧会话手工重命名别名 | 随 `t_unified_session_meta` 归档只读保留 |
| `t_schema_fingerprint` / `t_schema_drift_log` | **3 / 1** | 外部 Agent 私有存储结构漂移指纹 | 随私有存储扫描器退役而冻结废弃 |
| `t_tool_approval_log` | **0** | 工具级审批历史日志（空表） | 废弃，由业务级核心持久化表 `decisions` 正式替代 |
| `t_table_column_comments` | **64** | SQLite 表名与字段中文注释元数据字典 | **机制保留并升级**，确保新架构所有表与字段 100% 具备中文注释同步写入 |

### 4.2 现有实现 → 目标领域模型映射与新表设计规划

> **数据库命名与注释规范**：为保持工程规范统一并与存量 SQLite 库平滑共存，新架构核心表统一使用清晰的领域复数蛇形命名（通过 Alembic 统一管理），同时强制在 SQLAlchemy 模型 `comment` 属性及 SQLite 元数据字典表 `table_column_comments` 中完整写入**表名与每一个字段的中文注释**。所有连接在 `connect` 事件钩子中强制执行 `PRAGMA journal_mode=WAL`、`PRAGMA foreign_keys=ON`、`PRAGMA busy_timeout=15000`。

| 领域分层 | 目标实体 / 表名 | 对应现有实现 | 核心字段与约束要点（均含中文注释） | 引入阶段 |
| :--- | :--- | :--- | :--- | :---: |
| **项目与规划域** | `projects`（项目主表） | `t_workspace_bookmark`（仅路径收藏） | `id`, `slug`(唯一), `name`, `root_path`(规范化绝对路径), `git_remote`, `status`(`active/paused/archived`), `summary_next_action`, `version` | Phase 1 |
| **项目与规划域** | `tasks`（任务表） | 无 | `id`, `project_id`(FK `NOT NULL`), `parent_task_id`(自引用 FK，构成父子树), `title`, `goal`, `status`(`draft/ready/in_progress/blocked/waiting_decision/needs_revalidation/completed/cancelled`), `priority`, `active_revision_no`, `sort_order`, `version` | Phase 1 |
| **项目与规划域** | `task_dependencies`（任务轻量依赖表） | 无 | `id`, `project_id`(FK), `task_id`(FK), `depends_on_task_id`(FK), `dependency_type`；应用层强制校验同 Project 且无环（DAG） | Phase 1 |
| **项目与规划域** | `task_revisions`（任务修订历史表） | 无 | `id`, `task_id`(FK), `revision_no`(递增只追加), `goal_snapshot`, `acceptance_criteria_json`, `change_reason`, `created_by`, `created_at`；`UNIQUE(task_id, revision_no)` | Phase 1 |
| **项目与规划域** | `steps`（任务步骤表） | 无 | `id`, `project_id`(FK), `task_id`(FK), `title`, `status`(含 `needs_revalidation`), `seq_order`, `active_revision_no`, `assigned_runtime_hint` | Phase 1 |
| **项目与规划域** | `step_revisions`（步骤修订历史表） | 无 | `id`, `step_id`(FK), `revision_no`(只追加), `instruction_snapshot`, `change_reason`, `created_at` | Phase 1 |
| **会话与执行域** | `sessions`（协同会话表） | `t_unified_session_meta`（混杂） | `id`, `project_id`(FK **`NOT NULL`**), `primary_task_id`(FK 可空), `runtime_instance_id`(FK 可空), `source_channel`(`remote_link/mcp/local_runtime/hosted`), `external_session_ref`, `remote_url`, `title`, `reported_state`, `platform_state`, `status_seq` | Phase 1 |
| **会话与执行域** | `executions`（执行单表） | 内存 `TurnContext`（未落盘） | `id`, `project_id`(FK), `task_id`(FK), `step_id`(FK 可空), `session_id`(FK), `task_revision_no`, `reported_state`, `platform_state`, `started_at`, `finished_at` | Phase 2/4 |
| **会话与执行域** | `execution_attempts`（执行尝试表） | `active_processes`（纯内存） | `id`, `execution_id`(FK), `attempt_no`, `runtime_instance_id`(FK), `worker_pid`, `exit_code`, `error_summary`, `model_endpoint_json`(非敏感说明), `started_at`, `ended_at` | Phase 2/6 |
| **决策与产物域** | `decisions`（待决策事项表） | `ApprovalCoordinator`（内存态易失） | `id`, `project_id`(FK), `task_id`(FK 可空), `session_id`(FK 可空), `execution_id`(FK 可空), `decision_type`(`plan_confirm/scope_change/risk_gate/direction_choice`), `title`, `context_summary`, `options_json`, `recommended_option`, `impact_level`, `status`(`pending/resolved/expired/cancelled`), `resolution_payload`, `resolved_at` | Phase 3 |
| **决策与产物域** | `artifacts`（交付产物与关键变更表） | `workspace_git.py` 临时计算 | `id`, `project_id`(FK), `task_id`(FK 可空), `session_id`(FK 可空), `execution_id`(FK 可空), `artifact_type`(`plan_doc/git_diff_summary/test_report/markdown_note/link`), `title`, `relative_path`, `summary_content`, `metadata_json`, `created_at` | Phase 3 |
| **事件与审计域** | `events`（全局领域事件流表） | `RealtimeEventBus`（内存 `deque`） | `seq_id`(`INTEGER PRIMARY KEY AUTOINCREMENT`，全局严格递增游标), `event_id`(UUID), `project_id`(FK 可空), `entity_type`, `entity_id`, `event_type`, `actor_type`(`user/mcp_agent/runtime/system`), `payload_json`, `created_at`；**与业务表同事务写入** | Phase 1 |
| **Runtime 注册域** | `runtime_providers`（Runtime 产品定义表） | 硬编码 `agent_name` 字符串 | `id`(`claude_code/codex/opencode/antigravity`), `display_name`, `integration_track`, `capability_manifest_json`(L0–L3 声明), `is_enabled` | Phase 1 |
| **Runtime 注册域** | `machines`（宿主机器表） | 无（默认单机） | `id`, `name`, `hostname`, `os_platform`, `is_local`, `last_seen_at`（预留多机拓扑扩展锚点，当前默认注册 `local` 本机） | Phase 1 |
| **Runtime 注册域** | `runtime_instances`（运行实例与连接登记表） | `opencode_bridge` 等零散探测 | `id`, `provider_id`(FK), `machine_id`(FK), `project_id`(FK 可空), `endpoint_or_remote_url`, `health_status`, `capability_override_json`, `model_endpoint_descriptor_json`(可选非敏感说明，不存 Key), `last_heartbeat_at` | Phase 1 |
| **安全与支撑域** | `mcp_project_tokens` / `device_sessions` / `push_subscriptions` / `command_idempotency` | 现有 `t_device_session`、`t_push_subscription`、`t_command_idempotency` | MCP Token 存 SHA-256 哈希并强制绑定 `project_id NOT NULL`；设备会话与 Web Push 平滑承接存量记录 | Phase 1 / Phase 4 |

---

## 5. P00–P15 页面与现有前端代码逐项映射

### 5.1 前端现状总体诊断
当前 `frontend/` 是**零 `package.json`、零构建链、零 TypeScript、零组件框架**的原生 ES Modules 单页应用（共 1 个 `index.html` + 36 个 JS 文件 + 本地 `tailwind.min.js`）。整个 UI 结构锁死在“左侧会话列表抽屉 + 右侧单会话流式聊天气泡”。

对照用户全局前端规范与 `02_UI_Prototype_and_Interaction_Spec_v1.0.md`，前端需采用 **`React + TypeScript + Vite + Tailwind CSS + TanStack Query + Zustand + Lucide React`** 新建工程（建议置于 `frontend-v2/` 或将现目录平稳归档为 `frontend_legacy/` 后在 `frontend/` 初始化 Vite 工程，确保重构过渡期旧端可随时回退对照）。

### 5.2 P00–P15 逐页映射与改造策略表

| 页面编号 | 页面名称 | 目标路由 | 阶段优先级 | 现有前端可参考文件 / 资产 | 复用方式与 v1.3 交互收敛说明 |
| :---: | :--- | :--- | :---: | :--- | :--- |
| **P00** | 配对与登录页 | `/login`, `/pair` | MVP 必做 | `frontend/js/api.js`、`network-roaming.js`、`render-system-views.js` | **逻辑借鉴、UI 重写**：保留 `pair_code` 置换设备 Token 与 Tailscale/LAN 自动选路探测思路；修复 URL 明文 `?token=` 常驻问题 |
| **P01** | 全局驾驶舱首页（Cockpit Home） | `/` | MVP 必做 | 无对应视图（现有首页即空聊天欢迎屏） | **全新开发**：顶部连接/系统健康条 + 待决策紧急队列置顶卡 + 活跃 Project 卡片流 + 底部四栏固定导航（驾驶舱 / 项目 / 待决策 / 系统） |
| **P02** | 待决策中心列表（Inbox / Decisions） | `/decisions` | MVP 必做 | `frontend/js/render-approval-views.js`、`render-interactive-questions.js` | **概念升级重写**：剥离底层 Bash/文件读写工具级放行弹窗，重构为面向业务方向、范围变更、风险门禁的结构化 Decision 队列 |
| **P03** | 待决策详情与拍板抽屉/页 | `/decisions/:decisionId` | MVP 必做 | `frontend/js/actions-approval.js`、`command-queue.js` | **全新开发 + 幂等思路借鉴**：展示背景摘要、影响范围、结构化选项、推荐项与补充说明输入，提交走幂等键保护 |
| **P04** | 项目列表页（Projects） | `/projects` | MVP 必做 | `frontend/js/render-sidebar.js`（工作区折叠组） | **全新开发**：按活跃/暂停/归档筛选 Project，展示当前焦点 Task、待拍板数、活跃 Session/Remote 状态与下一步动作摘要 |
| **P05** | 新建 / 导入项目页 | `/projects/new` | MVP 必做 | `frontend/js/render-modal-views.js`（目录选择弹窗） | **全新开发**：支持从本机受控工作区根目录扫描导入 Git 仓库或手工登记 `root_path`，自动校验路径合法性 |
| **P06** | 项目工作台概览（Project Overview） | `/projects/:projectId` | MVP 必做 | 无 | **全新开发**：单项目主控台（焦点任务进度、待处理 Decision、`needs_revalidation` 警示条、活跃 Session 与一键跳官方 Remote、最近 Artifact） |
| **P07** | 项目任务树与规划页（Task Tree） | `/projects/:projectId/tasks` | MVP 必做 | 无 | **全新开发**：移动端友好的层级折叠任务树 + 状态胶囊 + 轻量前置依赖阻塞提示 + 快速新建子任务 |
| **P08** | 任务详情与修订页（Task Detail & Revision） | `/projects/:projectId/tasks/:taskId` | MVP 必做 | `frontend/js/markdown.js` | **全新开发**：展示 Task 目标、验收标准、Step 列表、Revision 历史对比；编辑保存生成新 Revision 并提示受影响下游项标为 `needs_revalidation` |
| **P09** | 项目会话列表页（Project Sessions） | `/projects/:projectId/sessions` | MVP 必做 | `frontend/js/render-session-card.js`、`session-status.js` | **局部视觉状态借鉴、结构重写**：严格挂在具体 Project 下，展示各 Session 绑定的 Runtime 图标、能力等级（L0–L3）、平台/上报双态及官方 Remote 链接 |
| **P10** | 会话协同详情页（Session Detail） | `/projects/:projectId/sessions/:sessionId` | MVP 必做（按 v1.3 裁剪） | `frontend/js/render-messages.js`（**大部分聊天/工具流不迁移**） | **按 v1.3 重定义**：不重造手机 Agent 聊天器与 Terminal；主区域展示该会话关联的 Task/Step、MCP 回写时间线、关联 Decision/Artifact 及 **“打开官方 Remote”** 按钮；仅对通过 L2 验证的 Runtime 显示受控指令发送框 |
| **P11** | 执行记录与尝试详情页（Execution Detail） | `/executions/:executionId` | 二阶段增强 | `frontend/js/render-step-cards.js`（仅状态时间线概念） | **全新开发**：展示 Execution 与历次 `ExecutionAttempt` 状态变迁、耗时、退出码与高层错误摘要（不默认渲染海量原始 stdout） |
| **P12** | 产物与关键变更查看页（Artifact Viewer） | `/artifacts/:artifactId` | MVP 必做 | `frontend/js/diff-viewer.js`、`markdown.js` | **算法逻辑可参考迁移**：展示结构化方案文档、测试摘要、Git 变更统计及高层 Diff 摘要（控制移动端单屏信息密度） |
| **P13** | 项目事件时间线审计页（Activity / Events） | `/projects/:projectId/events` | MVP 必做 | 无 | **全新开发**：基于 `events.seq_id` 分页与增量订阅展示可追溯的规划、修订、MCP 回写、拍板与执行里程碑 |
| **P14** | Runtime 与连接中心（Runtimes & MCP） | `/system/runtimes` | MVP 必做 | `frontend/js/render-model-picker.js`（**模型选择器废弃**） | **按 v1.3 全新开发**：展示 4 大 `RuntimeProvider` 的 `CapabilityManifest`（L0–L3）、`RuntimeInstance` 在线状态、Project MCP 配置片段一键复制及非敏感 `ModelEndpointDescriptor` 提示（不做模型供应商 Key 配置页） |
| **P15** | 系统设置、设备与备份页（System & Backup） | `/system/settings` | MVP 必做 | `frontend/js/push-client.js`、`sw.js`、`manifest.json` | **逻辑高价值迁移**：复用 Web Push VAPID 订阅逻辑、网络漫游诊断、已配对设备管理，新增一键触发 SQLite 热备与健康自检面板 |

---

## 6. 现有代码资产处置清单

### 6.1 高价值保留与封装复用（Keep & Adapt）
1. **`backend/core/logger.py`**：`SafeRotatingFileHandler`（完美规避 Windows 多句柄 `[WinError 32]` 轮转锁冲突）及 `log_structured_error` 三段式中文诊断日志汇聚 `logs/error.log` 机制 —— **直接保留作为新架构统一日志底座**。
2. **`backend/core/network.py`、`tailscale_auth.py`、`qr_box.py`、`awake_lock.py`**：Tailscale (`100.64.0.0/10`) / ZeroTier / LAN 自动探测、多宿主隔离绑定 `serve_multi_host`（拒绝裸绑 `0.0.0.0`）、60 秒一次性 `pair_code` SHA-256 哈希鉴权、CSWSH Origin 校验、Windows 防休眠锁 —— **直接保留并收紧权限细节**。
3. **`backend/core/push_service.py` + `backend/routes/push.py` + `data/vapid_keys.json` + `frontend/sw.js` 推送监听**：已跑通 iPhone iOS PWA 原生锁屏推送闭环 —— **保留底层通道，将触发源切换为 `Decision(pending)` 创建、Execution 异常阻塞等高价值领域事件**。
4. **`backend/engines/process_utils.py` + `run.py` / `stop.cmd`**：Windows `taskkill /F /T /PID` 进程树清理与本仓库进程命令行精准识别逻辑 —— **保留用于启动管理与未来可选的本地 Runtime 托管**。
5. **`backend/core/workspace.py`（路径规范化部分）+ `workspace_git.py`**：Windows 盘符/斜杠归一化 `normalize_project_path` 与 Git 状态/变更摘要只读提取 —— **补齐 `Project.root_path` 白名单与 symlink 越界防护后封装复用**。
6. **`frontend/` 静态 PWA 图标资产**：`apple-touch-icon.png`、`icon-192.png`、`icon-512.png`、`icon-1024.png`、`favicon.ico` —— **直接迁入新前端 `public/`**。

### 6.2 契约化重构（Refactor）
1. **`backend/adapters/opencode_bridge.py`** → 重构为符合 L0–L2 `CapabilityManifest` 的标准 `OpenCodeRuntimeAdapter`（仅走官方 HTTP Server / SDK 与 Web Remote，彻底剔除 `opencode.db` 直读）。
2. **`backend/engines/command_bus.py`** → 保留客户端幂等键（`Idempotency-Key`）与同实体并发互斥思想，重构沉入 Application 层事务工作单元（Unit of Work）。
3. **`backend/routes/websocket.py` + `engines/event_bus.py`** → 改造为“SQLite `events` 表同事务落盘 + 内存唤醒通知 + 客户端携带 `last_seq_id` 断线补拉（Catch-up）”的可靠事件流通道。

### 6.3 冻结并分阶段退役（Deprecate & Archive）
- `backend/adapters/antigravity_ls_bridge.py`、`antigravity_fork.py`、`antigravity_scanner.py`、`antigravity_parser.py`、`antigravity_tail_inspector.py`、`antigravity_model_resolver.py`（逆向 LS RPC 与私有 `.pb` 解析）；
- `backend/adapters/opencode_scanner.py`、`opencode_parser.py`、`backend/core/sqlite_snapshot.py`（直读外部 Agent 内部 SQLite）；
- `backend/adapters/claude_scanner.py`、`claude_parser.py`（直读 `~/.claude/projects/*.jsonl` 抽思维链与 Token）；
- `backend/adapters/schema_guard.py` 及 `schemas/*.json`（专为监控外部私有存储字段漂移而设，不再需要）；
- `backend/engines/approval.py`、`model_catalog.py`（纯内存工具级审批与底层模型枚举器）；
- `frontend/js/render-messages.js`、`render-turn-grouping.js`、`render-step-cards.js`、`render-model-picker.js`（旧版重度聊天流与模型切换 UI）。

---

## 7. 测试与构建基线盘点

### 7.1 静态编译与前端构建现状
- **后端语法编译验证**：已执行只读无副作用检查 `python -m compileall -q backend run.py`，退出码 `0`，无语法错误。
- **前端构建基线**：当前仓库**不存在 `package.json`、`tsconfig.json` 或 `vite.config.ts`**，无前端编译与类型检查基线，将在前端初始化阶段从零建立。
- **自动化测试执行说明**：受最高优先级全局操作规范 **“禁止主动执行自动化测试套件”** 与 **“禁止主动调用自动化浏览器子代理”** 约束，本轮 Phase 0 仅对 `tests/` 下全部测试文件进行了完整静态代码审查，未主动触发 `pytest` 运行（后续阶段如需运行特定验证命令，将在汇报中说明或由人工授权确认）。

### 7.2 现有 9 个测试文件逐项分类评估

| 测试文件 | 当前覆盖内容 | 重构期处置建议 |
| :--- | :--- | :--- |
| `tests/test_command_bus.py` | 命令总线幂等键与会话串行执行 | **保留思路并迁移**至新 Application 层幂等与并发控制测试 |
| `tests/test_push_and_resilient_schema.py` | Web Push 订阅管理（可留）+ 外部存储 Schema 漂移守卫（将弃用） | **拆分**：保留 Web Push 测试部分，退役私有存储漂移部分 |
| `tests/test_session_lifecycle.py` | 会话 CAS `status_seq` 状态跃迁单调性 | **借鉴迁移**：将其状态序号单调推进思想迁移至新 `Session` / `Execution` 状态机测试 |
| `tests/test_agentdeck.py` | 旧版综合路由与会话列表集成测试 | **冻结归档**，随新 `/api/v1/*` 契约建立全新领域与 API 测试套件 |
| `tests/test_antigravity_protocol_fallback.py` | Antigravity 逆向 LS 协议回退测试 | **随旧适配器一并退役** |
| `tests/test_bidirectional_sync_all_agents.py` | 基于本地私有文件扫描的多 Agent 双向同步 | **随私有扫描器一并退役** |
| `tests/test_opencode_models.py` | OpenCode 模型列表解析测试 | **退役**（新架构不做模型枚举面板） |
| `tests/test_rename_sync_all_agents.py` | 跨 Agent 本地会话重命名同步 | **退役** |
| `tests/test_shared_jsonl_reader.py` | 外部 Agent JSONL 文件尾部共享读取 | **退役** |

---

## 8. 技术验证风险登记表（SQLite / MCP / Worker JSONL / SDK Spike）

| 风险 ID | 风险维度 | 现状依据与具体风险点 | 概率 | 影响 | 最小验证方法与缓解方案 | 对应阶段 |
| :---: | :--- | :--- | :---: | :---: | :--- | :---: |
| **R-01** | **SQLite 并发写锁与事务边界** | 未来 FastAPI 用户请求、MCP Server 回写、后台巡检共用同一 `data/agentdeck.db`；若在事务内混入慢 I/O 会触发 `SQLITE_BUSY` | 中 | 高 | 统一使用短写事务 + `BEGIN IMMEDIATE`（必要处）+ `WAL` + `busy_timeout=15000` + `foreign_keys=ON`；严禁在数据库事务内发起外部 HTTP/子进程调用；备份统一调用 `sqlite3.Connection.backup()` | Phase 1 |
| **R-02** | **存量 224 条无路径会话的迁移冲突** | `t_unified_session_meta` 共 287 行，其中 **224 行 `project_path` 为空**；若强行迁入新 `sessions` 表将直接破坏 `project_id NOT NULL` 核心铁律 | **100%（已证实）** | 高 | 新建全新规范表（`projects`, `sessions` 等），**不原表硬改 `t_unified_session_meta`**；仅将 `t_workspace_bookmark` 与有效路径按需引导导入，旧表整体冻结为只读历史备查 | Phase 1 |
| **R-03** | **MCP Project Scope 鉴权与本机免密冲突** | 现有 `deps.py` 对 `127.0.0.1` 免 Token 放行；且本机尚未安装 `mcp` 官方包，各宿主 CLI 对 Streamable HTTP Header Token 传参格式存在差异 | 中 | **极高** | Phase 4 引入官方 `mcp` SDK，挂载独立路由 `/mcp` 并**强制校验 `Authorization: Bearer <project_scoped_token>`（禁用 `127.0.0.1` 免密）**；实测 Claude Code / OpenCode / Codex / Antigravity 本地 MCP 配置连通性 | Phase 4 |
| **R-04** | **Claude Code + CCSwitch 国内兼容模型 SDK 兼容性** | 用户在本机通过 CCSwitch 将 Claude Code 指向国内 Anthropic 兼容 API；`claude-agent-sdk` 在非官方端点下可能出现鉴权头差异、特殊 Beta 特性报错或流式事件不兼容 | 高 | 中 | 严格遵循 `04` v1.3：前期仅承诺 `claude remote-control`（L0）+ MCP（L1）；在 Phase 6 单独编写无侵入 Spike 验证本地环境变量/CCSwitch 生效下的 SDK 行为，绝不读取或改写 CCSwitch 配置，失败则诚实维持 L0+L1 | Phase 6 |
| **R-05** | **Windows 含空格/中文路径与子进程树残留** | 当前工作目录即含空格 `d:\Development Files\...`；Windows 下 `cmd.exe` / PowerShell 参数转义、stdout `gbk` vs `utf-8` 编码及孤儿进程易引发故障 | 中 | 高 | 所有子进程调用禁用 `shell=True`，统一传参数数组 `list[str]`，强制显式设置 `PYTHONIOENCODING=utf-8` 与 `encoding="utf-8", errors="replace"`，终止统一走 `taskkill /F /T /PID`；若 Phase 9 启用 Worker JSONL，帧协议增加魔数前缀过滤非 JSON 杂音 | Phase 1 / 6 / 9 |
| **R-06** | **Worker JSONL IPC 过早引入导致架构冗余** | `03` 曾把 Worker JSONL 放在 Phase 1，而 `05` 证实 OpenCode / Codex 自带 Server API、Claude 有 Remote + SDK | 中 | 中 | 采纳 `04` v1.3 裁定，将通用 Worker Supervisor 推迟至 Phase 9 按需启用，避免为不需要托管进程的 Runtime 维护双层状态机 | Phase 9（可选） |
| **R-07** | **新旧前后端切换期间的可用性断层** | 旧前端是零构建单页，新前端是 Vite + React 多路由驾驶舱；若直接覆盖会导致开发前半程失去可用界面 | 低 | 中 | 新后端 API 统一挂载在 `/api/v1/*`（与旧 `/api/*` 命名空间天然隔离）；新前端在独立工程目录构建并经特性开关或独立路径挂载，待 MVP 核心闭环验收后再正式接管根路径 `/` | Phase 1–5 |

---

## 9. ADR 架构决策冻结清单与待用户拍板事项（Gate 0）

### 9.1 已明确建议冻结的架构基线（ADR-001 ~ ADR-010）
- **ADR-001（架构定位）**：冻结为 **Project-first、Task-driven、Mobile-first 异步研发驾驶舱**，核心闭环聚焦“看全局进度、做业务拍板（Decision）、修订规划（Revision + `needs_revalidation`）、跳官方 Remote”。
- **ADR-002（Runtime 四级能力契约）**：采用 `04` v1.3 的 **L0 Remote Link / L1 Registry & MCP Status / L2 Programmatic Control / L3 Approval & Recovery** 能力分级，严禁再把逆向 LS RPC、内部 `.pb` 或私有 SQLite 直读带入核心层。
- **ADR-003（数据库与事务架构）**：采用 `SQLAlchemy 2.x + Alembic + SQLite WAL`，强制 `PRAGMA foreign_keys=ON`，所有核心业务写操作通过统一 Unit of Work 在**同一数据库事务内同时写入业务表与 `events` 表**，全表全字段配备中文注释。
- **ADR-004（状态双轨制）**：`Session` 与 `Execution` 均严格分离 `reported_state`（外部 MCP/Runtime 上报）与 `platform_state`（平台状态机综合判定），通过单调递增 `status_seq` 防止乱序覆盖。
- **ADR-005（模型与 CCSwitch 零侵入）**：不建 `model_providers` 核心控制表，不读取、不修改、不接管 CCSwitch 与任何供应商 API Key；仅保留可选非敏感 `ModelEndpointDescriptor` 说明字段。
- **ADR-006（存量数据库平滑共存）**：保留现有 `data/agentdeck.db` 中的 `t_device_session`（37 条）与 `t_push_subscription`（1 条）配对资产；`t_unified_session_meta`（含 224 条无路径历史记录）原表只读冻结，新领域启用全新规范表集合，确保 `sessions.project_id NOT NULL` 零妥协。
- **ADR-007（API 与 MCP 路由隔离）**：新 REST API 统一使用 `/api/v1/*`；MCP 端点独立挂载并强制校验绑定单一 `project_id` 的 Bearer Token，严禁复用本机 `127.0.0.1` 免密规则。
- **ADR-008（前端技术栈与视觉基线）**：新建 `React + TypeScript + Vite + Tailwind CSS + TanStack Query + Zustand + Lucide React` 工程，严格执行 `02` UI 规格书的深灰蓝黑设计语言（`#101318` 背景、`#171C23` 卡片、`#8BA9FF` 主强调色）。
- **ADR-009（日志与错误处理规范）**：复用并扩展 `backend/core/logger.py`，所有后端模块捕获异常必须携带中文上下文并通过 `log_structured_error` 统一汇聚写入 `logs/error.log`。
- **ADR-010（Worker Supervisor 延期策略）**：遵循 `04` v1.3，不在 Phase 1 建设通用 Worker JSONL 进程池，将其留作 Phase 9 可选扩展。

### 9.2 需用户在启动 Phase 1 前拍板确认的 4 个问题（Gate 0 确认项）
1. **【路线图顺序确认】**：是否同意以 **`04_AgentDeck_Architecture_and_Roadmap_v1.3.md`（Phase 0–11）作为主执行顺序**，并在 Phase 1 中一并完成 SQLite WAL/外键/在线热备与 Alembic 迁移基线建设（将 MCP 放在 Phase 4、Runtime/CCSwitch SDK Spike 放在 Phase 6、Worker JSONL 留作 Phase 9 可选）？
2. **【P10 页面形态确认】**：针对 `02`（带完整聊天流）与 `04` v1.3（不重造手机 Agent 聊天器）的差异，是否确认将 **P10 收敛为“会话编排上下文 + MCP 回写摘要 + 关联 Decision/Artifact + 一键打开官方 Remote（仅 L2 已验证 Runtime 显示受控指令框）”**？
3. **【新旧前端过渡目录策略】**：在构建新 React + Vite 前端期间，倾向于 **(A) 新前端先建在 `frontend-v2/`（旧 `frontend/` 保持原样对照，待 MVP 闭环完成后再切换）**，还是 **(B) 先将现有 `frontend/` 移动归档至 `frontend_legacy/`，直接在 `frontend/` 下初始化新工程**？
4. **【依赖锁定与测试验证授权】**：进入 Phase 1 时，需将已在环境中安装的 `sqlalchemy`、`alembic`、`pydantic-settings` 正式写入 `pyproject.toml`（无需联网下载新包，直至 Phase 4 引入 `mcp` SDK 时再申请安装）；同时请确认后续阶段完成编码后，是由您手动运行 `pytest`，还是授权我在阶段验收节点执行针对新增模块的定向验证命令？

---

## 10. Phase 1 具体实施计划（待确认后执行）

> 以下计划基于推荐的 **`04` v1.3 Phase 1（基础工程与核心领域模型 + SQLite 热备/迁移基线）** 制定，粒度精确到模块与文件。

### 10.1 阶段目标
在不破坏现有服务启动、设备配对与 Web Push 存量数据的前提下，建立清晰的新后端模块化目录骨架、SQLAlchemy 2.x + Alembic 迁移基线、SQLite 在线热备工具，落地核心领域模型（`Project`、`Task`、`TaskDependency`、`TaskRevision`、`Step`、`StepRevision`、`Session`、`Event` 及 `RuntimeProvider` / `Machine` / `RuntimeInstance` 最小注册表），实现**业务状态与 `events` 同事务原子持久化**及 **Revision 追加触发下游 `needs_revalidation`** 的纯领域闭环。

### 10.2 Phase 1 明确不做的事（严控范围）
- 不修改或删除现有旧适配器与旧前端代码，不改动暂存区中的 `start.cmd`；
- 不在本阶段接入真实外部 Runtime CLI/SDK，不开发 MCP Server（Phase 4），不开发通用 Worker Supervisor（Phase 9 可选）；
- 不在本阶段重写前端页面（Phase 2/5 推进），仅提供基础 `/api/v1/health` 与后台自检/备份基线接口。

### 10.3 目标后端目录结构与预计新增/修改文件清单

```text
backend/
├── v2/                                    # 或经确认直接采用模块化命名空间隔离新旧架构
│   ├── config.py                          # [新增] Pydantic Settings 统一配置（含含空格路径规范化）
│   ├── db/
│   │   ├── engine.py                      # [新增] SQLAlchemy Engine，强制 WAL / foreign_keys=ON / busy_timeout
│   │   ├── base.py                        # [新增] DeclarativeBase + 统一时间戳/版本号 Mixin + 中文注释注册
│   │   ├── uow.py                         # [新增] UnitOfWork：保障业务变更与 Event 写入同一 SQLite 事务提交
│   │   ├── backup.py                      # [新增] 基于 sqlite3.Connection.backup() 的在线热备与完整性校验服务
│   │   └── models/                        # [新增] 全表全字段含中文 comment 的 ORM 模型定义
│   │       ├── project.py                 # projects 表模型
│   │       ├── planning.py                # tasks, task_dependencies, task_revisions, steps, step_revisions
│   │       ├── session.py                 # sessions 表模型（强制 project_id NOT NULL + 双轨状态）
│   │       ├── runtime.py                 # runtime_providers, machines, runtime_instances
│   │       └── event.py                   # events 表模型（全局自增 seq_id）
│   ├── domain/
│   │   ├── enums.py                       # [新增] 状态枚举与 L0-L3 CapabilityManifest 值对象
│   │   ├── exceptions.py                  # [新增] 领域业务异常体系（统一接入中文 error.log）
│   │   └── services/
│   │       ├── revision_policy.py         # [新增] 追加 Revision 及下游 Task/Step 自动标 needs_revalidation 规则
│   │       └── dependency_graph.py        # [新增] 同 Project 校验与 TaskDependency 有向无环（DAG）检测
│   ├── application/
│   │   ├── project_service.py             # [新增] Project 注册（含 root_path 白名单与 symlink 安全校验）、列表与更新
│   │   ├── planning_service.py            # [新增] Task/Step 树创建、依赖绑定、Revision 发布用例
│   │   ├── session_service.py             # [新增] 归属 Project 的 Session 登记与双轨状态流转用例
│   │   └── runtime_registry_service.py    # [新增] 内置 4 大 RuntimeProvider（L0-L3 默认能力清单）与本机 Machine 种子初始化
│   └── api/
│       └── v1/
│           ├── router.py                  # [新增] /api/v1 总路由挂载
│           └── health.py                  # [新增] 数据库 WAL/外键状态检查与手动触发热备管理接口
├── alembic.ini                            # [新增] Alembic 迁移配置
└── migrations/                            # [新增] Alembic 迁移脚本目录（0001_phase1_core_foundation.py，含中文注释字典同步）
```

- **预计微调文件（仅在您确认进入 Phase 1 后执行）**：
  - `pyproject.toml`：补充声明已在环境中就绪的 `sqlalchemy>=2.0`、`alembic>=1.14`、`pydantic-settings>=2.0`；
  - `backend/app.py`：以非侵入方式挂载 `/api/v1` 路由，不影响现有旧路由运行。

### 10.4 Phase 1 完成验收标准（Gate 1）
1. 在备份副本上执行 `alembic upgrade head` 与 `alembic downgrade -1` 往返无误，存量 `t_device_session`（37 行）、`t_push_subscription`（1 行）、`t_unified_session_meta`（287 行）零损毁；
2. 数据库自检确认 `journal_mode=wal`、`foreign_keys=1`，且所有新增表与字段中文注释已完整同步至元数据注释表；
3. 领域不变量全部成立：无 `project_id` 创建 `Session` 被数据库与领域层双重拒绝；跨 `Project` 或成环 `TaskDependency` 被拦截；新增 `TaskRevision` 自动将关联下游置为 `needs_revalidation`；任何写操作若事件落盘失败则业务变更整体回滚；
4. `sqlite3.Connection.backup()` 在含空格工作目录 `d:\Development Files\...` 下成功生成一致性备份并通过 `PRAGMA integrity_check`；
5. 所有异常捕获均附带中文上下文并统一写入 `logs/error.log`。

### 10.5 回滚方案
- 数据库层：迁移前先通过 `backup()` 生成 `data/backups/pre_phase1_agentdeck.db` 快照；任何异常可通过 `alembic downgrade` 或直接恢复快照文件秒级还原。
- 代码层：全部新增代码隔离在独立包与 `/api/v1` 命名空间下，不触碰旧业务链路与暂存区。

