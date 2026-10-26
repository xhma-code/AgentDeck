# AgentDeck 交互式决策问答闭环架构实施计划 (Final Plan)

> **当前状态**：已通过 `/autoplan` 自动多维全景审查（CEO Review 9.5 / Design Review 9.8 / Eng Review 9.6 / DX Review 9.5）  
> **核心目标**：彻底解决多 Agent 引擎在遇到交互式问答时静默卡死的问题。打造**桌面级原生弹窗 + 3次指数退避重试 + 45s动态微进度条自动推进**的高鲁棒性闭环。

---

## 1. 核心需求与设计原则

1. **桌面原生级弹出体验（Native Modal UX）**：
   - 跨端置顶唤起模态弹窗（`fixed inset-0 z-50 bg-black/80 backdrop-blur-xs`），无论当前处于哪个会话，强聚焦决策；
   - 桌面 Web 端支持数字键（`1`/`2`/`3`）快速切换选项，`Enter` 秒级提交；
   - 移动端适配单手黄金操作区，大触控块（高度 ≥ 48px），触感反馈明确；严格遵循 `DESIGN.md` 色值规范。
2. **健壮提交与重试管道（Retry Pipeline）**：
   - 提交失败时执行 3 次指数退避重试（1s、2s、4s），界面展示实时重试进度；
   - 失败不丢失用户已选内容，支持手动一键重试；兼容 409 `FormAlreadySettledError` 作为幂等成功。
3. **敏捷倒计时兜底（Smart Fallback）**：
   - 默认 45 秒倒计时胶囊与微进度条动画；
   - 用户一旦产生点击或输入交互，倒计时立即挂起暂停（`paused`）；
   - 45 秒无任何交互自动采纳 `(Recommended)` 项并执行带重试的自动提交，消息流清晰留痕。
4. **底层协议兼容（Protocol Bridging）**：
   - OpenCode：通过官方 `/api/session/:id/form` 与 `/form/:id/reply` 接口解冻挂起的 Form；
   - Antigravity / Claude Code：通过事件总线注入结构化 Prompt 指令；
   - 状态机新增 `waiting_interaction`，消除假 running 状态。

---

## 2. 架构拆解与文件清单

### 后端架构变更
1. `backend/adapters/models.py`:
   - 增加 `waiting_interaction` 到 `SessionStatus` 枚举；
   - 定义 `UnifiedInteractiveForm` 与 `UnifiedInteractiveField` 数据结构。
2. `backend/adapters/tool_utils.py`:
   - 扩展 `extract_interactive_questions` 工具别名（支持 `question` / `form` 等）。
3. `backend/adapters/opencode_bridge.py`:
   - 新增 `reply_form(session_id, form_id, answer_dict)` 封装官方 `/form/:formID/reply`；
   - 新增 `fetch_pending_forms(session_id)` 提取处于挂起态的表单。
4. `backend/adapters/opencode_parser.py`:
   - 在 1.x 与 2.x 解析流中补齐 `extract_interactive_questions`，支持 `question` 工具。
5. `backend/engines/session_aggregator.py` & `backend/app.py`:
   - 新增 `/api/v1/sessions/{unified_id}/form/{form_id}/reply` 统一应答接口；
   - 在会话聚合器中将含有 pending form 的会话状态标记为 `waiting_interaction`。

### 前端架构变更
1. `frontend/index.html`:
   - 增加全局挂起的决策模态弹窗骨架容器 `#interactive-decision-modal`。
2. `frontend/js/modals.js` & `frontend/js/render-modal-views.js`:
   - 实现 `openInteractiveDecisionModal(formPayload)` 与 `closeInteractiveDecisionModal()`；
   - 实现 45s 动态倒计时计时器与暂停逻辑；
   - 绑定桌面键盘快捷键（1/2/3 单选与 Enter 提交）。
3. `frontend/js/actions-approval.js`:
   - 改造 `submitQuestionAnswers`，调用带 3 次指数退避重试的提交管道。
4. `frontend/js/network.js`:
   - 收到 WebSocket `FORM_PENDING` 或 `SESSION_UPDATED` 且状态为 `waiting_interaction` 时，自动呼出弹窗。

---

## 3. 落地实施执行清单

- [ ] **Task 1: 后端协议与接口补齐**
  - [ ] `backend/adapters/tool_utils.py`: 支持 `question` 工具提取
  - [ ] `backend/adapters/opencode_bridge.py`: 补齐 `reply_form` 与 `fetch_pending_forms`
  - [ ] `backend/adapters/opencode_parser.py`: 解析流注入 `questions`
  - [ ] `backend/app.py`: 注册 `/api/v1/sessions/{unified_id}/form/{form_id}/reply`
- [ ] **Task 2: 前端全局模态弹窗与键盘交互**
  - [ ] `frontend/index.html`: 挂载 `#interactive-decision-modal` 结构
  - [ ] `frontend/js/render-modal-views.js`: 渲染题目、选项、45s 倒计时条
  - [ ] `frontend/js/modals.js`: 倒计时逻辑、触摸暂停、键盘 1/2/3/Enter 监听
- [ ] **Task 3: 重试机制与自动推进**
  - [ ] `frontend/js/actions-approval.js`: 实现 3 次指数退避提交（1s/2s/4s）与 409 幂等容错
  - [ ] 45s 倒计时结束自动采纳 `(Recommended)` 提交
- [ ] **Task 4: WebSocket 跨页面穿透广播**
  - [ ] `frontend/js/network.js`: 监测到 `waiting_interaction` 跨页面自动唤醒弹窗

---

## GSTACK REVIEW REPORT
- Review Pipeline: `/autoplan` (v1.0.0)
- Phases Passed: CEO (9.5/10), Design (9.8/10), Eng (9.6/10), DX (9.5/10)
- Decision Register: All intermediate questions resolved; redundant escape/skip controls removed; fallback narrowed to 45s activity-paused auto-submit.
- Restore Point: `C:/Users/xhma10/.gstack/projects/AgentDeck/main-autoplan-restore-20261026.md`
