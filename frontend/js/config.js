/**
 * AgentDeck 配置与静态元数据定义
 */

export const DEFAULT_MODELS_BY_AGENT = {
  agy_cli: [
    { id: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (Medium)", tag: "均衡", is_recommended: true, is_default: true },
    { id: "gemini-3.8-flash-high", label: "Gemini 3.8 Flash (High)", tag: "极速", is_recommended: false },
    { id: "gemini-3.8-flash-low", label: "Gemini 3.8 Flash (Low)", tag: "超快", is_recommended: false },
    { id: "claude-sonnet-4-6-thinking", label: "Claude Sonnet 4.6 (Thinking)", tag: "Notice", is_recommended: true },
    { id: "claude-opus-4-6-thinking", label: "Claude Opus 4.6 (Thinking)", tag: "Notice", is_recommended: true },
    { id: "gpt-oss-120b-medium", label: "GPT-OSS 120B (Medium)", tag: "Notice", is_recommended: true },
  ],
  agy_ide: [
    { id: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (Medium)", tag: "均衡", is_recommended: true, is_default: true },
    { id: "gemini-3.8-flash-high", label: "Gemini 3.8 Flash (High)", tag: "极速", is_recommended: false },
    { id: "gemini-3.8-flash-low", label: "Gemini 3.8 Flash (Low)", tag: "超快", is_recommended: false },
    { id: "claude-sonnet-4-6-thinking", label: "Claude Sonnet 4.6 (Thinking)", tag: "Notice", is_recommended: true },
    { id: "claude-opus-4-6-thinking", label: "Claude Opus 4.6 (Thinking)", tag: "Notice", is_recommended: true },
    { id: "gpt-oss-120b-medium", label: "GPT-OSS 120B (Medium)", tag: "Notice", is_recommended: true },
  ],
  claude_code: [
    { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", tag: "默认" },
    { id: "claude-opus-4-7", label: "Claude Opus 4.7", tag: "深度" },
    { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5", tag: "极速" },
    { id: "sonnet", label: "Sonnet (Latest)", tag: "" },
    { id: "opus", label: "Opus (Latest)", tag: "" },
  ],
  opencode: [
    { id: "opencode/big-pickle", label: "Big Pickle", tag: "默认", is_recommended: true, quota: 1.0 },
    { id: "opencode/deepseek-v4.1-flash-free", label: "DeepSeek V4.1 Flash Free", tag: "免费", is_recommended: false, quota: 1.0 },
    { id: "opencode/glm-5.3-flash-free", label: "GLM-5.3-Flash Free", tag: "免费", is_recommended: false, quota: 1.0 },
    { id: "opencode/nemotron-3.5-super-vl-free", label: "Nemotron 3.5 Super VL Free", tag: "免费", is_recommended: false, quota: 1.0 },
    { id: "opencode/flint-40b-free", label: "Flint 40B Free", tag: "免费", is_recommended: false, quota: 1.0 },
    { id: "opencode/beam-free", label: "Beam Free", tag: "免费", is_recommended: false, quota: 1.0 },
    { id: "opencode/mimo-v2.6-flash-free", label: "MiMo-V2.6-Flash Free", tag: "免费", is_recommended: false, quota: 1.0 },
    { id: "opencode/nemotron-3.5-lightning-free", label: "Nemotron 3.5 Lightning Free", tag: "免费", is_recommended: false, quota: 1.0 },
    { id: "opencode/nemotron-3-ultra-free", label: "Nemotron 3 Ultra Free", tag: "免费", is_recommended: false, quota: 1.0 },
    { id: "anthropic/claude-sonnet-4-5", label: "Claude Sonnet 4.5", tag: "" },
    { id: "google/gemini-2.5-pro", label: "Gemini 2.5 Pro", tag: "" },
    { id: "openai/gpt-5", label: "GPT-5", tag: "" },
  ],
};


export const ALL_EXECUTION_MODES = [
  { value: "auto", label: "⚡ 全自动", fullLabel: "⚡ 全自动 (Auto)" },
  { value: "accept_edits", label: "✏️ 自动改", fullLabel: "✏️ 自动改 (Edit)" },
  { value: "plan", label: "📋 规划", fullLabel: "📋 规划 (Plan)" },
  { value: "ask", label: "🛡️ 审批", fullLabel: "🛡️ 审批 (Ask)" },
];


export const MODES_BY_AGENT = {
  agy_cli: ALL_EXECUTION_MODES,
  claude: ALL_EXECUTION_MODES,
  claude_code: ALL_EXECUTION_MODES,
  opencode: [
    { value: "auto", label: "⚡ 全自动", fullLabel: "⚡ 全自动 (Auto)" },
    { value: "ask", label: "🛡️ 审批", fullLabel: "🛡️ 审批 (Ask)" },
  ],
  agy_ide: [
    { value: "auto", label: "⚡ 全自动", fullLabel: "⚡ 全自动 (克隆接管)" },
    { value: "accept_edits", label: "✏️ 自动改", fullLabel: "✏️ 自动改 (克隆接管)" },
    { value: "plan", label: "📋 规划", fullLabel: "📋 规划 (克隆接管)" },
    { value: "ask", label: "🛡️ 审批", fullLabel: "🛡️ 审批 (克隆接管)" },
  ],
};


export const GIT_STATUS_BADGES = {
  M: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  A: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  U: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  D: "bg-rose-500/20 text-rose-300 border-rose-500/40",
  R: "bg-purple-500/20 text-purple-300 border-purple-500/40",
};


export const AGENT_META = {
  agy_ide: {
    label: "Antigravity",
    short: "AGY",
    badgeClass: "bg-purple-500/15 text-purple-300 border-purple-500/30",
  },
  agy_cli: {
    label: "Antigravity CLI",
    short: "AGY",
    badgeClass: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  },
  antigravity: {
    label: "Antigravity",
    short: "AGY",
    badgeClass: "bg-purple-500/15 text-purple-300 border-purple-500/30",
  },
  claude_code: {
    label: "Claude Code",
    short: "Claude",
    badgeClass: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  },
  opencode: {
    label: "OpenCode",
    short: "OC",
    badgeClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  },
};


export const MODEL_CONTEXT_LIMITS = {
  "claude-sonnet-5-5-high": 200000,
  "claude-opus-5-5-high": 200000,
  "claude-sonnet-5-5-medium": 200000,
  "claude-sonnet-4-6": 200000,
  "claude-opus-4-7": 200000,
  "claude-haiku-4-5-20251001": 200000,
  "gemini-4-argon-high": 1000000,
  "gemini-4-argon-medium": 1000000,
  "gemini-3.8-flash-high": 1000000,
  "gemini-2.5-pro": 1000000,
  "gemini-2.5-flash": 1000000,
  "gpt-oss-120b-medium": 128000,
  "gpt-4o": 128000,
  "gpt-4o-mini": 128000,
};


export const DEFAULT_PROJECT_SESSION_LIMIT = 8;
