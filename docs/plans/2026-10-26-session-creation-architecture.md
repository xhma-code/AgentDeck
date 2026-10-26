# 新建会话创建架构改进计划 (v2 — CEO 评审后)

<!-- /autoplan restore point: 原始 v1 见 ~/.gstack/projects/AgentDeck/main-autoplan-restore-20261026b.md -->

## 目标（v2 改写）

**会话创建从「返回即成功」升级为「真相状态机」**：每个会话从创建起有 `creating / live / degraded / dead` 状态并落库（existing agentdeck.db），前端一切展示由这张真相表驱动。原 C2（幂等持久化）、C4（degraded 标记）并入状态机主线；C1/C3/C5 为并发卫生项。

## What already exists（0B 杠杆图 — 不重建）

| 子问题 | 已有代码 | 复用方式 |
|---|---|---|
| 持久化 | agentdeck.db SQLite WAL + `t_unified_session_meta`（schema.py） | 状态机直接加列，不建新表 |
| 会话锁 | CommandBus 会话级 asyncio.Lock（command_bus.py:30,42-45） | 下沉到 `send_prompt_to_session` 内部，REST/WS 共用 |
| 状态广播 | WebSocket 事件环 + SESSION_STATE_SNAPSHOT（websocket.py:54-61） | 状态机变更直接发事件，前端已有快照恢复 |
| 软删/墓碑 | `mark_session_deleted`（database.py:332-352） | `dead` 状态复用同套扫描过滤 |
| 降级探测 | agy LS 进程存活探测 + 降级链（process_pool.py:236-267） | degraded 判定复用同模式 |
| 幂等处理 | `_processed_commands`（command_bus.py:28） | 持久化到库，不换算法 |

## Dream State（0C）

```
CURRENT                      THIS PLAN                    12-MONTH IDEAL
创建=乐观数据+内存态          创建落库含状态列              状态机成为全生命周期地基
失败=幻影静默存在           creating/live/degraded/dead   挂起/审批/联邦也走同一状态机
熔断=重启全丢               幂等表+状态列持久化            多 PC 联邦读同一真相状态
```

## Alternatives considered（0C-bis，自动决策：差异化覆盖 → 取完备性 A）

| 方案 | 完备性 | 决策 |
|---|---|---|
| A) 状态机持久化（理想架构）| 10/10 | ✅ **采纳**（P1；CEO voice critical #1；SQLite 在手，无新进程模型，<1d CC）|
| B) 原补丁清单（最小可行：幂等表+C4 标记）| 7/10 | 不采纳——三个补丁散落三处，6 个月后仍是出血点（git log 显示该链路 3 次返修）|
| C) 同步化创建（等真实 raw_id，超时硬失败）| 8/10 | ✗ 否决（一票否决：你不能单机用户在 opencode server 瞬断时无法新建；但 degraded 首条系统消息吸收其诚实性）|
| D) kill 幻影会话（检测失败删 meta 重建）| 8/10 | ✗ 否决（比 degraded 重 UI 扰动；用户会丢草稿初始 prompt）|

## 改进项（v2 重排）

**主线 S：状态机**
- S1 `t_unified_session_meta` 加 `status` 列（creating/live/degraded/dead）+ 变更全走单函数 `set_session_status()` 发 WS 事件【schema.py, database.py, process_pool.py, websocket.py】
- S2 幂等表持久化：`_processed_commands` 落 `t_command_idempotency`，保留最近 1000 条【command_bus.py, database.py】

**卫生项**
- C1 锁下沉：`send_prompt_to_session` 内部取会话锁，REST/WS 两路自然串行（P5：唯一入口收锁）【process_pool.py】
- C3 direct 任务 TTL 回收：**空闲信号 = 连续 2 个 poll 周期无新输出且上次事件 >5min**（非拍脑袋 10min），回收时广播【process_pool.py】
- C5 排队可见：并发额度满出新会话→WS 推「队列中」事件；上限提取到 pyproject/settings【process_pool.py, modals.js, websocket.py】
- C7 run.py 合并修复：per-server bind 异常隔离（单 host 失败降级单网卡+告警）+ 端口占用识别增强（pid 检验，本机路径命 cmd string 即为本项目）【run.py】

**降级语义（前提 P3 已确认）**
- C4 → 并入 S1：opencode HTTP 创建失败仍降级 CLI 直连，但状态=is degraded 且首条系统消息告知用户【opencode_adapter.py, process_pool.py】

**测试 T1**
- T1-1 创建失败→degraded→首条系统消息（集成）
- T1-2 幂等表重启后拒绝重放（集成）
- T1-3 REST+WS 同会话并发发送→串行不交错（并发）
- T1-4 direct 空闲 TTL 回收（单元）

## 性能基线（CEO voice #6 采纳）

验收红线：创建响应 p99 < 500ms（本地）；WS 状态事件延迟 < 1s。基线取改动前后各 20 次 `POST /api/v1/sessions` 实测均值。

## 路线叙事（CEO voice #7 采纳）

本计划 = 地板强化（≤1 周）。地板后进入差异化工期：多 PC 联邦（TODOS.md 已列）是「手机关会话、回家继续」的护城河项，状态机正是联邦共享状态的前置。

## NOT in scope（含否决理由）

- 消息正文迁移自有库：独立大件，状态机不依赖它
- 独立 worker / 消息队列：否决理由=P2 + 触发条件未现（若单人并发 >8 或需跨机调度再立项——已记录触发条件）
- 审批挂起持久化：ask 模式重构单独立项
- 托盘/语音/联邦：TODOS.md 既存项

## Error & Rescue Registry

| 失败点 | 异常 | 谁捕获 | 用户看见 |
|---|---|---|---|
| opencode HTTP POST 失败（创建） | TimeoutError/HTTPError | process_pool L259-267 | 首条系统消息「底层创建失败，CLI 直连中」 |
| LS start_cascade 拒绝 | Connect-RPC 错误 | 降级链 | 顶层提示 + 自动落 agy_cli（已有） |
| 首轮 remap 失败 | FileNotFoundError | alias 表兜底 | 会话显示临时 ID（已有，保留） |
| REST /send 绕锁并发 | 无（设计缺陷） | S1+C1 后并入锁 | 排队串行，无 500 |
| 幂等表读写失败 | sqlite3.Error | 包裹 try，降级内存表 | 无感（日志告警）|
| 单 host bind 失败 | OSError | C7 捕获单 server | 仍可用另一个地址，页面横幅提示 |

## Failure Modes Registry（top 3）

1. **状态与底层不一致**（会说 live 实际已死）→ 心跳对账：TTL 扫描器定期重验 active 集，不一致即降级并广播
2. **degraded 会话永久 degraded** → 提供显式重试入口（再次 POST 创建探测）
3. **排队事件风暴**（每解额度发一次）→ 合并去抖，500ms 内只发最新态

## CEO DUAL VOICES — CONSENSUS TABLE（[subagent-only]，Codex 配额耗尽至 11-22）

| 维度 | Claude | Codex | Consensus |
|---|---|---|---|
| 前提有效? | 部分（P3 已过用户 gate） | N/A | N/A |
| 问题对吗? | 重构为状态机（10x 框架） | N/A | N/A |
| 范围校准对? | 是（选择性扩展） | N/A | N/A |
| 备选充分? | v1 缺 → v2 已补 4 项 | N/A | N/A |
| 竞争风险covered? | 补路线叙事 | N/A | N/A |
| 6 个月轨迹稳? | 是（状态机支撑联邦） | N/A | N/A |

## Decision Audit Trail

| # | Phase | Decision | 分类 | 原则 | 理由 | 被否 |
|---|---|---|---|---|---|---|
| D0 | CEO | Codex 全程缺席降级 subagent-only | 机械 | — | 配额耗尽 | — |
| D1 | CEO | 采纳方案 A 状态机重写目标 | 机械 | P1 完备 | v1 补丁散落=出血点 | B/C/D |
| D2 | CEO | C1 锁下沉 send_prompt_to_session | 自动 | P5 显式/P4 DRY | 唯一入口收锁 | REST 入口收锁 |
| D3 | CEO | C3 空闲信号=2 周期无输出+5min | 自动 | P1 | 10min 拍脑袋会杀合法长任务 | 10min TTL |
| D4 | CEO | T2 并入 C7（同文件） | 自动 | P2 煮湖 | 同文件零成本 | 延后 |
| D5 | CEO | 补性能基线红线 | 自动 | P1 | 功能验收缺回归红线 | — |
| D6 | CEO | 补路线叙事（卫生→联邦） | 自动 | P3 | 测试/仪式感不三占一 | — |
| D7 | 设计 | 乐观对象以 creating 渲染 | 自动 | P5 | 现状 RUNNING=假状态 | 保持 RUNNING |
| D8 | 设计 | 7 状态四格 UI 映射表 | 自动 | P1 | 五态不可实现即返工 | — |
| D9 | 设计 | 创建快返回 + degraded 事件驱动 | 自动 | P1/P5 | 25s 黑暗谷 | 同步等待 |
| D10 | 设计 | degraded 可继续发消息 | 自动 | P1 | CLI 直连真实可用 | 禁用输入 |
| D11 | 设计 | token 映射 + 前端触点清单 | 自动 | P5 | 防实施者漏改 | — |

---

## Phase 2 — 设计评审（[subagent-only]，mockup 二进制缺失→文字评审，classifier=APP UI）

**初评：3/10**——后端手术刀齐全，前端触点零图纸。10 分 = 每个状态四格画面 + 前端触点清单（文件+函数级）。

### Pass 1 信息架构 4/10 → 自动修
会话列表第一眼应答「有没有真的在跑」，现在乐观对象无条件标 RUNNING（modals.js L884-887 假状态）= 与状态机目标直接冲突。**修：乐观对象以 creating 渲染（骨架徽标非绿点），等 WS/快照确认才转 live。**

### Pass 2 状态覆盖 5 缺 5 → 自动加「UI 状态映射表」

| 状态 | 列表徽标 | 会话 header | 聊天流 | 可否发消息 |
|---|---|---|---|---|
| creating | 灰骨架 + 「启动中」(mono 11px) | 「正在启动 agent…」细脉冲条（100ms 即反馈，DESIGN.md #4） | prompt 气泡正常显示 | ✅ |
| live | sky 绿点 | 无提示 | 正常 | ✅ |
| degraded | **amber** 徽标「直连」 | banner：底层创建失败，本轮走 CLI 直连 + 重试按钮 | 首条系统消息（幂等去重：带唯一 id，快照恢复不重复插） | ✅（placeholder 提示直连态） |
| dead | 列表收进「已归档」组 | 横幅「会话已结束」 | 只读 | ❌ 输入框禁用 |
| queued（新，C5） | 「排队中」zinc 徽标 | 同左 | — | ✅（提示将排队） |
| TTL 回收后（C3） | 回到 live 静置态 | 「已休眠，发消息即可唤醒」一次 | — | ✅ |

### Pass 3 旅程 4/10 → 自动修
情感弧断点 = **25 秒黑暗谷**（POST timeout 25000ms，degraded 语义后端秒级可判但用户等 25s）。**修：创建接口快速返回 creating（<500ms 红线本已有），降级判定异步化走 WS 事件**——这是 CEO 否决的 Option C 的残骸清理。

### Pass 4 AI slop 8/10
无新增装饰组件，徽标复用列表现有状态点语言；无卡片、无渐变。通过。

### Pass 5 DESIGN.md 对齐 8/10 → 映射定案
- degraded = `--accent-amber`（复用现有审批警告/重连提示 token）
- dead/error = `--accent-rose`
- live/creating 活动态 = `--accent-sky`
- 徽标字号 = 现有 mono 11px 微标签规范（DESIGN.md §3）

### Pass 6 响应式/a11y 8/10 → 自动定案
徽标热区 ≥40px；banner 含 aria-live="polite"；遵守 safe-area/100dvh；mobile 底部「+ 新对话」吸附位不动，创建结果反馈不进横滚区。

### Pass 7 决疑（逐条自动定，taste 项 → 终局 gate）

| 待决 | 自动定案 | 原则 |
|---|---|---|
| degraded 能否继续发消息 | 能（CLI 直连真实可用），placeholder 说明 | P1 完备 |
| 乐观对象 vs WS 快照合并规则 | 字段级 last-write-wins，后端 WS 值优先；title 冲突后端赢 | P5 显式 |
| dead 触发方 | 仅系统判定；用户删除走既有确认流，不复用 dead | P5 |
| onLine 误判 | 改判依据 = WS 连接状态，非 navigator.onLine | P5 |
| degraded 重试入口 | header banner 内扁平按钮（amber，非 hold 长按——二门操作） | P5 |

前端触点清单（防 miss）：`modals.js confirmCreateNewSession`、`renderSessionList`、`sessions.js` header/banner/徽标、`network.js` WS 状态事件消费、新增 `session-status.js`（四态渲染单函数，用户字段一律 textContent 渲染——防 XSS，工程 D2）。

### 设计共识表（Codex 缺席 → N/A 列）

| 检查 | Claude | Codex | Consensus |
|---|---|---|---|
| 首屏品牌可辨? | ✅（既有 App UI） | N/A | N/A |
| 状态四格覆盖? | ✅ v3 已修 | N/A | N/A |
| AI slop 硬拒? | 无 | N/A | N/A |
| DESIGN.md token 对齐? | ✅ | N/A | N/A |
| 25s 黑暗谷? | ✅ 已修（快速 creating + 事件驱动） | N/A | N/A |
| 移动/PWA/Tailscale 专项? | ✅ 已纳入 | N/A | N/A |

---

## Phase 3 — 工程评审（[subagent-only]，已读实际代码：process_pool/command_bus/stream_runner/schema/database/opencode_bridge/run.py）

### 架构 ASCII（新增组件设为 ★）

```
 routes/sessions.py (REST)      routes/websocket.py (WS)
          │ execute_command              │
          ▼                              ▼
 process_pool.create_session ★set_session_status∈database.py★
          │ 状态写库(纯同步) ──回调注入★──▶ event_bus.broadcast(不进追赶缓冲)
          ├─ OpenCode: http_bridge.create_session→(sid,via_http)★ / 失败→orchestrator 打 degraded
          ├─ agy: LS 探测(同步<100ms) 降级链不变
          └─ upsert_session_meta ★UPDATE 显式保留 status 列★
                     │ send_prompt_to_session ★会话锁唯一下沉点★
                     ▼
          StreamRunner.dispatch_prompt ─ӷ semaphore 等待点★广播 queued★
                     └─ run_agent_turn_stream / watch_direct_turn_progress(TTL 长尾兜底)
```

### 工程自动决策（13 项，全部采纳）

| # | 严重度 | 决策 | 修改点 |
|---|---|---|---|
| E1 | critical | `OpenCodeHttpBridge.create_session` 改返回 `(sid, via_http)`，失败不打假 ID，orchestrator 生成假 ID 同时打 degraded | opencode_bridge.py, opencode_adapter.py |
| E2 | critical | D9 定案：创建响应=同步 route 判定（LS 探测 <100ms）+ 带 `creating` status；降级链换 agent 时走 t_session_alias remap，id 冲突后端赢 | process_pool.py |
| E3 | critical | `set_session_status` 放 database.py 纯同步写（UPDATE 参数化），广播走回调注入（仿 bump_version_fn）；`_upsert_meta` 显式保留 status；加 `status_seq` 单调 CAS 防陈旧写 | database.py, process_pool.py, stream_runner.py |
| E4 | high | 心跳对账用 `http_bridge.is_server_running()` 真探测，不得走 1s mtime 缓存 | sqlite_snapshot.py 边界注释 |
| E5 | high | opencode 降级首轮 raw_id 回填：remap 机制扩到 opencode CLI（现仅 agy_cli 有），remap 时迁移 status 列 | stream_runner.py, database.py |
| E6 | high | C3 重定义：watcher（12s 退出）为事实源；process_pool 只挂 5min 长尾兜底（watcher 退出失败路径）；TTL loop 注入 clock/sleep | process_pool.py, stream_runner.py |
| E7 | medium | abort_session 后 `await wait_for(proc.wait(), 2.0)` reap | process_pool.py |
| E8 | medium | S2 幂等表：epoch 时间戳 + 重启后 error 态不命中允许重放；payload 白名单字段+截断 | command_bus.py, database.py |
| E9 | medium | 状态事件不进 200 条追赶缓冲；快照本身携带 status | websocket.py, event_bus.py |
| E10 | medium | broadcast 1.5s→0.3s 且状态事件 shield 后台发 | event_bus.py |
| E11 | medium | queued 判定挂 semaphore `wait_to_acquire` 处；上限提取 settings | stream_runner.py, process_pool.py |
| E12 | medium | WS 事件 payload 仅 status 枚举 + unified_id（异常细节只进日志）；SESSION_CREATED meta 带 status | stream_runner.py, command_bus.py |
| E13 | medium | 边界声明：agentdeck.db 必须本地盘（WAL 网络盘失谐），联邦只同步快照 | 计划边界注记 |

### 工程共识表（Codex 缺席）

| 维度 | Claude | Codex | Consensus |
|---|---|---|---|
| 架构合理? | 方向✅，3 处落点硬伤已修 E1-E3 | N/A | N/A |
| 测试覆盖完? | 补 4 规格（假绿防护）后足 | N/A | N/A |
| 性能风险covered? | E9/E10 修后covered | N/A | N/A |
| 安全威胁covered? | E12 + textContent 后 covered | N/A | N/A |
| 错误路径齐? | E1/E5 补上后齐 | N/A | N/A |
| 部署风险可控? | 是（无迁移反转：加列默认 live 兼容） | N/A | N/A |

### 部署注记

SQLite 加列默认兼容（旧行 status 默认 live / NULL 视为 creating），无回滚表脚本风险；回滚 = git revert + 列无害残留。rollout 顺序：S1 状态列 → S2 幂等 → 卫生项。

| D12 | 工程 | bridge 返回 tuple + degraded 判定点后移适配层 | 自动 | P1 | 现状 opencode 路径 silent 假 ID |
| D13 | 工程 | set_session_status 落 database.py + status_seq CAS | 自动 | P5/P1 | 分层割裂与 stale write |
| D14 | 工程 | C3 重定义 watcher 为唯一事实源 | 自动 | P4 DRY | TTL=死代码 |
| D15 | 工程 | T1 假绿防护规格（PYTEST marker 旁路） | 自动 | P1 | mock 短路=假绿 |
| D16 | 工程 | 幂等 epoch + error 不锁定重放 | 自动 | P1 | 2am 静默故障 |
| D17 | 工程 | WS payload 只枚举 + textContent | 自动 | P1 安全 | 信息泄漏面收敛 |

---

## Phase 3.5 — DX 评审（[subagent-only]，8 维初评 4/10 → 修后预期 9/10）

| # | 维度 | 初评 | 发现 → 自动决策 |
|---|---|---|---|
| 1 | TTHW | 4/10 | README「核心特性」与新状态模型脱节 → 文档触点清单（D20） |
| 2 | 内部 API 工效 | 8/10 | `SessionStatus=Literal` 集中定义（D24）；queued 快照来源=semaphore 现状重算 |
| 3 | 错误消息 | 4/10 | 三要素扫雷：dead 零原因零恢复路径（critical）→ reason 枚举（crash/timeout/killed）随快照携带（D18）；degraded 系统消息改人话：「OpenCode 服务未响应，已自动改用备用方式连接。会话可正常使用，点击重试恢复完整功能」（D19）；queued 带数字「已达到同时运行上限（8），本会话将自动开始」 |
| 4 | 文档 | 2/10 | 文档触点清单：README 特性段 + Troubleshooting 加 degraded 行 + schema 注释 + session-status.js 模块头（指向 Pass 2 表）（D20） |
| 5 | 可观测性 | 5/10 | set_session_status 强制结构化单行日志 `unified_id status from→to seq reason`（E14，D21）；心跳对账降级也要日志 |
| 6 | 逃生舱 | 3/10 | `AGENTDECK_MAX_SUBPROCESSES` + TTL/去抖参数 env override > settings 默认（D22）；默认值写入 C5 验收 |
| 7 | 升级路径 | 7/10 | 定案冲突：旧行统一补 UPDATE 为 live，NULL→live（否则旧会话永久骨架/spin）（D23）；WAL 本地盘校验进 `--doctor`（D25） |
| 8 | 开发环境摩擦 | 8/10 | 零新依赖 ✅；端口 pid 检验失败给通用文案「端口被占用，尝试 --port」（D26） |

### DX Implementation Checklist
- [x] 精确文案三条验收（degraded/dead+reason/queued 带数字）— session-status.js + network.js + _handle_stream_line SESSION_CRASHED 路径
- [x] 文档触点清单 5 处更新（README 特性段 + Troubleshooting degraded 行 + schema.py 注释 + session-status.js 模块头 + --doctor WAL 行）
- [x] E14 结构化状态迁移日志（database.set_session_status）
- [x] env override：AGENTDECK_MAX_SUBPROCESSES / AGENTDECK_IDLE_POLL_SECONDS / AGENTDECK_IDLE_MIN_SECONDS
- [x] 旧行 UPDATE live 一次性补丁 + --doctor WAL 校验

---

## 实施记录（2026-10-27）

**状态：已完成，全测试 96 passed（tests/ 全套 pytest，含新增 tests/test_session_lifecycle.py T1-1~T1-5）。**

| 项 | 落点 |
|---|---|
| S1 状态机 | schema.py（status/status_seq/t_command_idempotency DDL+注释）、database.py（迁移+set_session_status CAS+resolve_unified_id 修复+幂等 DAO）、process_pool.py（状态回调广播、create_session 三态流转）、session_aggregator.py（lifecycle 透出）、event_bus.py（track_in_buffer=false + 快照 session_statuses） |
| E1/E5 | opencode_bridge.create_session→(sid, via_http)、opencode_adapter 不再伪造假 ID；remap 整行迁移自动携带 status |
| S2 | command_bus.py（done→ACK 回放 / error→放行重放、payload 白名单摘要） |
| C1/C3/C5/C7 | process_pool（send 锁下沉、长尾 2 周期+5min 回收、E7 reap）、stream_runner（SESSION_QUEUED）、run.py（per-server bind 隔离、--doctor WAL 校验） |
| 前端 | 新增 session-status.js（四态单渲染，textContent 防 XSS）；modals.js（乐观对象 lifecycle=creating）；network.js（STATUS_CHANGED/QUEUED 消费+快照对账）；render-session-card.js 徽标；render-views.js header 横幅+重试 |
| 测试 | tests/test_session_lifecycle.py 6 用例（T1-1 降级快返回+广播+不进缓冲、T1-2 重启幂等、T1-3 双路串行、T1-4 长尾回收、T1-5 remap 迁移） |

**实施中发现并修复**：database.py 大段插入曾吞掉 `resolve_unified_id` 签名（测试 T1-5 命中，已复原）。

TTHW：现状 ~10min（3 步起服务）——不在本计划 blast radius，维持；无退化承诺。

---

## GSTACK REVIEW REPORT

| 评审 | 状态 | 发现 | 处置 |
|---|---|---|---|
| CEO (Phase 1) | clean_v2 | 7（voice Claude，Codex 缺席） | 全部rogate改入 v2 目标/alternatives/基线/叙事 |
| 设计 (Phase 2) | clean | 6 | 全部修入（状态映射表/乐观渲染/25s 谷/token 映射） |
| 工程 (Phase 3) | clean | 16（3 critical） | 全部修入 E1-E14 + 测试假绿防护 |
| DX (Phase 3.5) | clean | 9 | 全部修入 D18-D26 |

VERDICT: APPROVED — cross-model review absorbed（Codex 配额耗尽至 11-22 降级 subagent-only；mockup 视觉层面未验证为唯一遗留）。

批准：2026-10-27，用户 gate D4=A。实施基线 = 本文件 v2 改进项 + Phase 2/3/3.5 checklist。




