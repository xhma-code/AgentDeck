# ⚡ AgentDeck v2.0

基于 **Tailscale** 私有局域网的多 Agent（**Antigravity IDE / Antigravity CLI / Claude Code / OpenCode**）移动端统一中控台。

让你离开电脑（在沙发、通勤或会议间隙）也能通过手机浏览器实时查看桌面端 AI 编程会话、审批高危工具调用、阅读长篇 Markdown 计划文档并无缝接力对话。

---

## ✨ 核心特性

- **四大 Agent 统一会话池**：
  - **Antigravity IDE (`~/.gemini/antigravity`)**：实时镜像桌面端 IDE 会话；在手机端发送指令时支持**安全克隆至 CLI (`Fork-to-CLI`)** 继续执行，彻底杜绝双进程写同一 SQLite 引发的脑裂损坏。
  - **Antigravity CLI (`~/.gemini/antigravity-cli`)**：原生 `stream-json` 双向管道与多模型热切换。
  - **Claude Code (`~/.claude/projects`)**：实时解析 `.jsonl` 会话流，支持 `--permission-prompt-tool stdio` 逐条工具权限拦截与审批。
  - **OpenCode (`~/.local/share/opencode/opencode.db`)**：短连接毫秒级只读聚合 SQLite 会话，并对接 `opencode serve` HTTP/SSE 接口。
- **零公网暴露安全架构**：
  - 严格仅绑定本机 Tailscale 虚拟网卡 IP（`100.x.y.z`）与 `127.0.0.1`，**严禁绑定 `0.0.0.0`**。
  - 启动时生成 **60 秒一次性配对码 (`pair_code`)** 与本地离线 SVG 二维码，手机扫码后置换 SHA-256 长期访问令牌，防截图重放与跨站 WebSocket 劫持 (CSWSH)。
- **专为手机窄屏打磨的阅读与操控体验**：
  - **单列 Unified Diff**：文件编辑操作自动生成 390px 窄屏友好的单列代码变更对比。
  - **高危操作长按 0.8s 确认**：自动识别 `rm -rf`、`git reset --hard`、`taskkill` 等破坏性命令，手机端需长按填充进度条方可放行。
  - **内置全屏 Markdown 阅读器**：对话流中生成或修改的 `.md` 文档（如 `implementation_plan.md`）一键弹出全屏排版阅读。
  - **4KB 大输出保护 + 分页 + GZip**：单步工具输出超过 4KB/100 行自动折叠截断（支持按需拉取全量），首屏消息分页加载，配合 GZip 压缩与 `mtime` 内存缓存，确保移动端 60fps 流畅滚动。
  - **Windows 防休眠锁 (`SetThreadExecutionState`)**：支持手机端远程开关电脑防休眠（允许显示器熄屏，保持后台网络与 Agent 持续运行）。

---

## 🚀 快速开始

### 1. 环境要求
- **Python**：`>= 3.11`
- **操作系统**：Windows 10/11（同样兼容 macOS / Linux，Windows 下额外提供防休眠锁与进程树递归清理）
- **网络（可选但推荐）**：电脑与手机安装并登录同一个 [Tailscale](https://tailscale.com/) 账号

### 2. 安装依赖
```bash
pip install -e ".[dev]"
```

### 3. 启动服务
Windows 下可直接双击运行 `start.cmd`，或在终端执行：
```bash
python run.py
```
- **仅本机调试**：`python run.py --local-only`
- **自定义端口**：`python run.py --port 8766`
- **停止服务**：运行 `stop.cmd`（或在终端按 `Ctrl+C`）

启动后终端将直接打印 **4 大 Agent 状态体检表** 与 **手机扫码配对二维码**，也可在电脑浏览器打开 `http://127.0.0.1:8765/` 查看高清 SVG 配对二维码。

---

## 📂 目录结构

```text
AgentDeck/
├── backend/
│   ├── adapters/               # 四大 Agent 存储与协议适配器
│   │   ├── base.py             # 统一会话/消息模型、单列 Diff、高危命令分级与工程目录识别
│   │   ├── antigravity_adapter.py  # Antigravity IDE/CLI 适配器与 Fork-to-CLI 安全克隆
│   │   ├── claude_adapter.py   # Claude Code JSONL 解析与系统噪音清洗
│   │   └── opencode_adapter.py # OpenCode SQLite 只读查询与 HTTP 桥接
│   ├── core/
│   │   ├── database.py         # 本地元数据库 (data/agentdeck.db) 与外部 SQLite 快照防锁死引擎
│   │   ├── logger.py           # 三段式中文诊断日志 (Problem + Cause + Fix)，错误统一汇聚至 logs/error.log
│   │   └── tailscale_auth.py   # Tailscale 网卡探测、60s 一次性配对码鉴权与 Windows 防休眠锁
│   ├── engines/
│   │   └── process_pool.py     # 多 Agent 流式子进程池、工具审批状态机与 watchfiles 实时广播
│   └── app.py                  # FastAPI 路由、GZip 压缩、WebSocket 快照同步与 PWA 静态托管
├── frontend/                   # 零构建依赖的移动端响应式 PWA 工作台
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   └── manifest.json
├── tests/
│   └── test_agentdeck.py       # 单元与集成测试套件
├── run.py                      # 一键启动入口
├── start.cmd / stop.cmd        # Windows 快捷启停脚本
└── pyproject.toml              # 项目元数据与依赖声明
```

---

## 🧪 运行测试

```bash
pytest -q
```
