# AgentDeck → Antigravity 开发交接包
**版本：1.0｜日期：2026-10-31｜目标：将本次会话形成的重构方案完整交给 Antigravity 执行**

## 1. 交接包内容与阅读顺序

请按以下顺序阅读，三份文件共同构成开发基线：

1. `01_Architecture_Review_v1.1.md` — **架构与产品边界的最高依据**。决定产品定位、技术栈、领域模型、Agent 接入方式、数据与安全原则。
2. `02_UI_Prototype_and_Interaction_Spec_v1.0.md` — **页面和交互依据**。决定信息架构、页面清单、移动端/桌面端布局、任务树、任务详情、状态和交互验收。
3. `03_Phased_Development_Implementation_Plan_v1.0.md` — **实施顺序和工程验收依据**。决定 Phase 0–13 的开发步骤、交付物、验收门槛和提交规范。

若文件之间发生冲突：
- 产品边界、领域不变量、架构选择：以架构决策书 v1.1 为准。
- UI 页面、视觉、交互：以 UI 规格书 v1.0 为准，但不得违反架构决策书。
- 实施顺序、阶段验收：以分阶段实施计划 v1.0 为准。
- 如果发现真实仓库与文档不符，先报告差异，不要自行假设文档已实现。

## 2. 项目基本信息

- GitHub 仓库：`xhma-code/AgentDeck`
- 默认分支：`main`
- 开发环境：Windows
- 目标平台：Windows 优先，兼容 macOS / Linux
- 产品形态：单用户自托管，SQLite；手机端通过 PWA 访问（网络访问建议经 Tailscale）
- 前端方向：React + TypeScript + Vite
- 后端方向：Python + FastAPI + Pydantic v2 + SQLAlchemy 2.x + Alembic
- 数据库：SQLite WAL
- 架构：模块化单体；托管 Agent Runtime 使用独立 Worker
- Agent 接入：AgentDeck MCP Server 是外部 Agent 使用平台能力的 P0 入口
- 代码仓库当前实际状态、当前分支名和 HEAD 必须由 Agent 在 Phase 0 重新检查；不要把文档中的目标架构误认为仓库现状。

## 3. 核心产品定义（不可擅自改变）

AgentDeck 是 **Agent 协作管理平台**，不是 Agent Runtime、Harness、IDE 或通用 Workflow Engine。

平台管理：
- Project、Task、Step、Revision；
- Session 的 Project 归属与可选 Task 关联；
- Execution、Attempt、状态和历史；
- Decision、人工检查点与审计；
- Artifact 登记和追溯；
- Revision 修改后的下游影响分析；
- 多 Agent 项目上下文连续性。

Agent / Runtime 自己负责：
- 模型推理、Agent Loop、规划、工具调用；
- Runtime 内部上下文、子 Agent 和自身权限策略。

关键不变量：
1. 每个 Session 必须属于一个 Project；Task 关联可空。
2. Task / Step / Session / Execution / Decision / Artifact 的跨项目引用必须由服务端校验。
3. Session、Execution、Attempt 是三个独立实体。
4. Task Tree 是默认视图；只有真实依赖才建 DAG，不强制所有任务形成 DAG。
5. 修改 Step 产生新 Revision，保留历史；下游标记 `needs_revalidation`，不自动重跑。
6. Agent 报告进度不等于平台验收；Agent 不能仅凭“完成”把 Task 置为已验收。
7. Event 与业务状态同事务持久化；Decision 必须落库，服务重启后仍可见。
8. MCP 是 Agent 与 AgentDeck 协作的工具入口，不是控制 Antigravity IDE 完整 Session 生命周期的 API。
9. 不自建通用 Agent Loop；不把 Antigravity 私有 Language Server RPC 作为核心依赖。
10. 未真实验证的 Runtime 能力标记 `unverified`；Mock 不能冒充真实集成。
11. 不要求旧版 API/数据库/UI 永久兼容，但不得静默丢弃历史用户数据。
12. UI 默认突出任务状态、待决策和交付物；不展示思考链、Token 或冗长工具调用流水。

## 4. 已明确的 UI 方向

用户认可此前讨论的移动端任务管理和任务详情风格：
- 移动端优先、暗色、克制、清晰、单手操作；
- 首页是工作入口，不是大画布或监控大屏；
- 首页优先展示“需要我处理”和“正在执行”；
- 任务详情有概览、步骤、动态、交付物四个视图；
- 默认纵向任务树/依赖链，可折叠节点；
- 只有复杂真实依赖才提供可缩放依赖图；
- 节点操作包括查看执行记录、修改步骤、查看下游影响、与 Agent 讨论；
- 不突出 Agent 内部工具调用、Token 和思考过程。

UI 规格书中列出的 P00–P15 页面和交互规则应完整落实。不能为了方便开发自行删掉页面或改变已确认的布局逻辑；若需调整，先提交设计差异和原因。

## 5. Git 与数据安全规则

- 只允许在用户指定的功能分支开发，绝不直接修改或提交到 `main`。
- 当前分支名未知，必须先在本地仓库执行 `git status --short --branch`、`git branch --show-current`、`git log -1 --oneline` 确认。
- 如果当前在 `main`，停止，不要创建分支或切换分支，先让用户确认要使用的分支名。
- 不要擅自合并 PR、推送 main、删除分支或重写 Git 历史。
- 每个阶段独立提交；提交前展示变更文件清单和测试结果。
- 不执行破坏性数据库迁移，不删除旧数据；需要迁移时先备份并提供计划，等待用户确认。
- 不把真实 Token、API Key、密码、敏感 Prompt 写入仓库或日志。
- 不要因架构文档给出目标目录就机械重建项目；先审查现有代码，保留可复用实现。

## 6. Antigravity 操作方法（推荐）

### 第一步：准备文件
1. 将本交接包解压到本地。
2. 打开 AgentDeck 的本地 Git 仓库目录（不是单独打开交接包目录）。
3. 将本交接包中的三个 Markdown 文件复制到仓库：
   `docs/agentdeck-handoff/`
4. 将本文件 `00_README_AND_EXECUTION_PROMPT.md` 也复制到该目录。
5. 确认这些文档在本地可被 Antigravity 读取。建议把它们纳入当前功能分支的文档提交；若暂时不想提交，也可先作为本地参考文件。

### 第二步：在 Antigravity 打开项目
1. 使用 Antigravity 桌面客户端打开 AgentDeck 仓库根目录。
2. 确认终端当前目录是仓库根目录。
3. 确认当前 Git 分支是指定功能分支，而不是 `main`。
4. 在 Antigravity Agent 对话中，先让它读取 `docs/agentdeck-handoff/` 下的四份文件。
5. 将下面“首轮执行 Prompt”完整发送给 Agent。

### 第三步：首轮只审查，不开发
首轮只允许 Phase 0。Agent 必须检查真实代码、分支、依赖、数据库、前后端、测试和运行方式，输出仓库现状审查、目标文件映射、差异、风险和 Phase 1 文件级计划。不得修改业务代码、数据库结构或执行破坏性操作。

### 第四步：人工确认后逐阶段实施
收到 Phase 0 报告后，用户先审阅：
- 技术栈是否需要按现有仓库做合理适配；
- 旧代码哪些保留、重构、弃用；
- SQLite / Worker / MCP 技术验证计划是否可执行；
- 当前功能分支与提交策略是否正确。

确认后再明确授权 Phase 1。后续每阶段完成都先验收，再进入下一阶段。

## 7. 首轮执行 Prompt（请原样发送给 Antigravity）

你现在接手 AgentDeck 的重构开发。请先完整阅读以下文件：

- `docs/agentdeck-handoff/00_README_AND_EXECUTION_PROMPT.md`
- `docs/agentdeck-handoff/01_Architecture_Review_v1.1.md`
- `docs/agentdeck-handoff/02_UI_Prototype_and_Interaction_Spec_v1.0.md`
- `docs/agentdeck-handoff/03_Phased_Development_Implementation_Plan_v1.0.md`

这些文档共同构成当前产品和技术基线。你必须区分“目标设计”和“仓库已经实现的现状”，不能把文档中的目标当成现有功能。

**当前只执行 Phase 0：仓库复核与架构冻结。不得开始 Phase 1，不得修改业务代码。**

请按以下顺序完成：

1. 检查当前工作目录、Git 仓库、当前分支、HEAD commit、工作区状态、远程地址。绝不切换或提交到 `main`。如果当前在 `main`，立即停止并向用户报告。
2. 检查仓库完整目录结构、README、依赖清单、启动脚本、环境变量、前后端入口。
3. 检查现有数据库结构和数据。只读检查；不删除、不重建、不迁移。
4. 检查当前前端框架、路由、页面、组件、样式、PWA、API Client、实时通信和测试。
5. 检查当前后端 API、领域模型、数据库访问、Agent Adapter、MCP、进程管理和安全边界。
6. 运行现有测试与构建；记录实际命令、退出码、摘要。无法运行时说明环境阻塞，不得写“通过”。
7. 建立“目标设计 → 当前代码”的逐项映射，标记：可复用 / 需修改 / 需新增 / 建议弃用 / 尚未确认。
8. 特别审查：Session 必须归属 Project、Session/Execution/Attempt 分离、Revision 追加式、Decision 持久化、Event 原子性、SQLite WAL、Worker JSONL、MCP Project scope、Task Tree 与可选 DAG。
9. 识别架构文档中尚未验证的技术风险，不得将其写成已经解决。
10. 输出 Phase 0 报告，至少包含：
   - 当前仓库事实摘要和目录结构；
   - 当前分支/HEAD/工作区状态；
   - 前端、后端、数据库、Runtime 集成现状；
   - 目标领域模型与现有实现映射表；
   - P00–P15 页面与现有前端文件映射表；
   - 可复用代码清单；
   - 必须重构/新增/弃用清单；
   - 测试和构建基线；
   - 风险登记表（概率、影响、验证方式）；
   - ADR 待确认项；
   - Phase 1 的具体技术验证计划、涉及文件和验收标准。
11. 将报告保存为 `docs/implementation/phase-0-repository-audit.md`（如果目录不存在可以创建文档目录，但不要改业务代码）。
12. 完成后停止，向用户总结审查结论和风险，等待明确授权 Phase 1。

**严格禁止：**
- 修改业务代码、数据库 schema 或用户数据；
- 删除或重命名现有文件；
- 安装/升级项目依赖；
- 自动格式化全仓库；
- 创建或切换 Git 分支；
- 修改 `main`；
- 推送、合并 PR 或重写 Git 历史；
- 把 Mock 结果说成真实 Runtime 验证；
- 未经确认开始 Phase 1。

遇到任何需要改变架构决策、删除旧数据、变更分支或执行破坏性操作的情况，先停止并说明原因、影响和备选方案。

## 8. 阶段推进规则

每个阶段开始前，Agent 先报告：
- 本阶段目标和不做什么；
- 前置条件；
- 预计修改的文件；
- 数据库/API/协议变化；
- 风险和回滚方法；
- 测试计划。

每阶段结束必须报告：
- 变更文件清单；
- 实现内容与未实现内容；
- migration 说明；
- API/MCP 契约变化；
- 实际执行的测试命令、退出码和结果；
- Windows PowerShell 启动/测试结果；
- UI 阶段提供 360px 手机和 1440px 桌面截图；
- 风险、已知缺陷和下一阶段前置条件；
- 独立 Git commit hash。

没有用户验收，不进入下一阶段。不得一次性并行推进多个高风险阶段。

## 9. 重要说明

本交接包包含本次重构讨论形成的三份正式设计文档，以及把它们交给 Antigravity 的操作指引和首轮 Prompt。它不包含完整聊天逐字记录，也不代表仓库已经按方案改造。所有现状必须以 Antigravity 对本地仓库的实际检查为准。
