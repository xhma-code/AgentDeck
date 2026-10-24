/**
 * AgentDeck 对话消息长流轮次聚合与工具性质分类器
 */

import { state } from "./state.js";
import { getBasename, getDurationStrFromDates, formatDuration } from "./utils.js";

export function isReadOnlyTool(tc) {
  if (tc && typeof tc.is_read_only === "boolean") return tc.is_read_only;
  const name = String((tc && tc.name) || tc || "").toLowerCase().replace(/-/g, "_");
  return (
    name.includes("read_file") ||
    name.includes("view_file") ||
    name.includes("grep") ||
    name.includes("glob") ||
    name.includes("search_web") ||
    name.includes("file_search")
  );
}

export function groupMessagesIntoTurns(messages) {
  const turns = [];
  let i = 0;

  while (i < messages.length) {
    const m = messages[i];
    if (m.role === "user") {
      turns.push({ type: "user", item: m });
      i++;
      continue;
    }

    // 收集紧随其后的连续 assistant 步骤，合并为一个回答轮次 (Assistant Turn)
    const assistantSteps = [];
    while (i < messages.length && messages[i].role !== "user") {
      assistantSteps.push(messages[i]);
      i++;
    }

    const firstStep = assistantSteps[0];
    const lastStep = assistantSteps[assistantSteps.length - 1];
    const turnKey = `${state.selectedSessionId || "sess"}_turn_${firstStep.step_index}`;

    // 找到最后一个包含非空 content 的步骤下标
    let lastContentIdx = -1;
    for (let idx = assistantSteps.length - 1; idx >= 0; idx--) {
      if ((assistantSteps[idx].content || "").trim()) {
        lastContentIdx = idx;
        break;
      }
    }

    // 划分：主回复正文 vs 收纳进折叠栏的中间过程步骤
    const primarySteps = [];
    const processRecords = [];
    const turnQuestions = [];
    let thinkingCount = 0;
    let toolCount = 0;
    let hasHighRisk = false;
    const editedFilesSet = new Set();
    const mdFilesMap = new Map();

    const recordMdCandidate = (rawPath) => {
      if (!rawPath) return;
      const clean = String(rawPath).split("#")[0].split("?")[0].trim();
      if (/\.(md|markdown|mdx)$/i.test(clean)) {
        const base = getBasename(clean);
        if (base.toLowerCase() !== "skill.md") {
          mdFilesMap.set(clean, base);
        }
      }
    };

    assistantSteps.forEach((step, idx) => {
      if (Array.isArray(step.questions) && step.questions.length > 0) {
        turnQuestions.push(...step.questions);
      }
      const hasThinking = !!(step.thinking && step.thinking.trim());
      const tools = Array.isArray(step.tool_calls) ? step.tool_calls : [];
      const hasTools = tools.length > 0;
      const text = (step.content || "").trim();

      if (hasThinking) thinkingCount++;
      if (hasTools) {
        toolCount += tools.length;
        for (const tc of tools) {
          if (tc.risk_level === "high") hasHighRisk = true;
          const fPath = tc.file_path || "";
          if (tc.diff_text || /edit|write|replace|create/i.test(tc.name || "")) {
            const targetName = getBasename(fPath || tc.args_summary);
            if (targetName && targetName.length < 60) {
              editedFilesSet.add(targetName);
            }
          }
          recordMdCandidate(fPath);
          if (/\.(md|markdown|mdx)$/i.test(tc.args_summary || "")) {
            recordMdCandidate(tc.args_summary);
          }
        }
      }

      // 扫描正文中引用的 file:///.../*.md 链接（兼容含空格路径）
      if (text) {
        const linkMatches = text.matchAll(/\[[^\]]*\]\((file:\/\/\/[^\n)]+\.md(?:#[^\n)]*)?)\)/gi);
        for (const lm of linkMatches) {
          recordMdCandidate(lm[1]);
        }
      }

      // 判断该 step 的文本是最终主回复还是调用工具前的简短过渡说明
      const isSubstantialReport =
        text.length >= 160 || /```|^#{1,4}\s|\n\|.*\|.*\n/m.test(text);
      const isPrimaryText =
        text && (idx === lastContentIdx || (isSubstantialReport && !hasTools));
      const isIntermediateNote = text && !isPrimaryText;

      if (hasThinking || hasTools || isIntermediateNote) {
        processRecords.push({
          step_index: step.step_index,
          created_at: step.created_at,
          thinking: hasThinking ? step.thinking.trim() : "",
          thinking_len: step.thinking_len || (hasThinking ? step.thinking.trim().length : 0),
          intermediateNote: isIntermediateNote ? text : "",
          tool_calls: tools,
        });
      }

      if (isPrimaryText) {
        primarySteps.push({
          step_index: step.step_index,
          content: text,
          created_at: step.created_at,
        });
      }
    });

    let durationStr = "";
    if (firstStep && lastStep && firstStep.created_at && lastStep.created_at) {
      durationStr = getDurationStrFromDates(firstStep.created_at, lastStep.created_at);
    }
    if (!durationStr && processRecords.length > 0) {
      let totalToolSec = 0;
      for (const rec of processRecords) {
        for (const tc of rec.tool_calls || []) {
          if (tc.duration_str) {
            const m = tc.duration_str.match(/^([\d.]+)\s*s$/);
            if (m) totalToolSec += parseFloat(m[1]);
          }
        }
      }
      if (totalToolSec > 0) {
        durationStr = formatDuration(totalToolSec * 1000);
      }
    }

    turns.push({
      type: "assistant_turn",
      turnKey,
      durationStr,
      processRecords,
      primarySteps,
      questions: turnQuestions,
      thinkingCount,
      toolCount,
      editedFiles: Array.from(editedFilesSet),
      mdFiles: Array.from(mdFilesMap.entries()).map(([path, name]) => ({ path, name })),
      hasHighRisk,
      createdAt: lastStep.created_at || firstStep.created_at,
      firstStepIndex: firstStep.step_index,
      lastStepIndex: lastStep.step_index,
    });
  }

  return turns;
}
