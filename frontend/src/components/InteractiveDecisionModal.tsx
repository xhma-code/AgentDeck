import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Minimize2,
  PauseCircle,
  PlayCircle,
  RefreshCw,
  ShieldAlert,
  Sparkles,
} from 'lucide-react';
import { apiClient } from '../api/client';
import type { DecisionItem } from '../types/api';

interface InteractiveDecisionModalProps {
  decision: DecisionItem | null;
  pendingCount: number;
  onResolved: () => void;
}

export const InteractiveDecisionModal: React.FC<InteractiveDecisionModalProps> = ({
  decision,
  pendingCount,
  onResolved,
}) => {
  const [selectedOptionId, setSelectedOptionId] = useState<string | null>(null);
  const [customNote, setCustomNote] = useState<string>('');
  const [minimized, setMinimized] = useState<boolean>(false);
  const [pausedByInteraction, setPausedByInteraction] = useState<boolean>(false);
  const [remainingSec, setRemainingSec] = useState<number>(45);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string>('');
  const idempotencyKeyRef = useRef<string>('');
  const lastDecisionIdRef = useRef<string>('');

  const isHighRisk = useMemo(() => {
    if (!decision) return false;
    return (
      decision.risk_level === 'high' ||
      decision.risk_level === 'critical' ||
      decision.decision_type === 'risk_gate'
    );
  }, [decision]);

  const canAutoSubmitRecommended = useMemo(() => {
    if (!decision) return false;
    return (
      !isHighRisk &&
      decision.timeout_policy === 'auto_submit_recommended' &&
      Boolean(decision.recommended_option_id) &&
      decision.timeout_sec > 0
    );
  }, [decision, isHighRisk]);

  // 切换到新决策时初始化默认推荐项、倒计时与专属幂等键（同一决策失败重试时保留答案与幂等键）
  useEffect(() => {
    if (!decision) {
      lastDecisionIdRef.current = '';
      return;
    }
    if (decision.id !== lastDecisionIdRef.current) {
      lastDecisionIdRef.current = decision.id;
      const defaultOpt =
        decision.recommended_option_id ||
        decision.options.find((o) => o.is_recommended)?.option_id ||
        decision.options[0]?.option_id ||
        null;
      setSelectedOptionId(defaultOpt);
      setCustomNote('');
      setMinimized(false);
      setPausedByInteraction(false);
      setRemainingSec(decision.timeout_sec > 0 ? decision.timeout_sec : 45);
      setSubmitError('');
      idempotencyKeyRef.current = `dec-${decision.id}-${Date.now()}`;
    }
  }, [decision]);

  const handleSubmit = useCallback(
    async (resolvedBy: 'user_manual' | 'timeout_auto_recommended') => {
      if (!decision || submitting) return;
      const targetOptionId =
        resolvedBy === 'timeout_auto_recommended'
          ? decision.recommended_option_id
          : selectedOptionId;

      if (!targetOptionId && !customNote.trim()) {
        setSubmitError('请先选择一个决策方案或填写补充意见');
        return;
      }

      setSubmitting(true);
      setSubmitError('');
      try {
        await apiClient.resolveDecisionWithRetry(decision.id, {
          chosen_option_id: targetOptionId,
          custom_note:
            resolvedBy === 'timeout_auto_recommended' && !customNote.trim()
              ? '45 秒倒计时结束，按策略自动采纳推荐方案'
              : customNote.trim(),
          resolved_by: resolvedBy,
          idempotency_key:
            idempotencyKeyRef.current || `dec-${decision.id}-${Date.now()}`,
        });
        onResolved();
      } catch (err) {
        const msg =
          err instanceof Error
            ? err.message
            : '网络请求失败，您的答案已保留，请点击重试';
        setSubmitError(msg);
        setPausedByInteraction(true);
      } finally {
        setSubmitting(false);
      }
    },
    [customNote, decision, onResolved, selectedOptionId, submitting],
  );

  // 45s 动态倒计时（仅当允许自动推荐提交、未最小化、未被用户交互暂停且未在提交中时递减）
  useEffect(() => {
    if (
      !decision ||
      !canAutoSubmitRecommended ||
      minimized ||
      pausedByInteraction ||
      submitting
    ) {
      return;
    }
    if (remainingSec <= 0) {
      void handleSubmit('timeout_auto_recommended');
      return;
    }
    const timer = window.setTimeout(() => {
      setRemainingSec((prev) => Math.max(0, prev - 1));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [
    canAutoSubmitRecommended,
    decision,
    handleSubmit,
    minimized,
    pausedByInteraction,
    remainingSec,
    submitting,
  ]);

  // 桌面端键盘快捷键：1/2/3 选择方案，Enter 提交，Esc 最小化保留答案
  useEffect(() => {
    if (!decision || minimized) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isTyping =
        target &&
        (target.tagName === 'TEXTAREA' ||
          target.tagName === 'INPUT' ||
          target.isContentEditable);

      if (e.key === 'Escape') {
        e.preventDefault();
        setPausedByInteraction(true);
        setMinimized(true);
        return;
      }

      if (!isTyping && ['1', '2', '3', '4'].includes(e.key)) {
        const idx = Number(e.key) - 1;
        const opt = decision.options[idx];
        if (opt) {
          e.preventDefault();
          setPausedByInteraction(true);
          setSelectedOptionId(opt.option_id);
        }
        return;
      }

      if (e.key === 'Enter' && (!isTyping || e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        void handleSubmit('user_manual');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [decision, handleSubmit, minimized]);

  if (!decision) return null;

  if (minimized) {
    return (
      <div
        style={{
          position: 'fixed',
          bottom: 76,
          right: 20,
          zIndex: 990,
          background: 'var(--bg-panel)',
          border: '1px solid var(--accent-blue)',
          borderRadius: 'var(--radius-lg)',
          padding: '12px 16px',
          boxShadow: 'var(--shadow-modal)',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          maxWidth: 420,
        }}
      >
        <AlertTriangle size={20} color="#f59e0b" />
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 13 }}>
            待确认决策 ({pendingCount})：{decision.title}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            已暂停倒计时并保留您刚才的选择
          </div>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => setMinimized(false)}
        >
          展开拍板
        </button>
      </div>
    );
  }

  const totalSec = decision.timeout_sec > 0 ? decision.timeout_sec : 45;
  const pct = Math.min(100, Math.max(0, (remainingSec / totalSec) * 100));

  return (
    <div className="modal-backdrop">
      <div
        className="modal-panel"
        onMouseDown={() => setPausedByInteraction(true)}
        onTouchStart={() => setPausedByInteraction(true)}
      >
        {/* 顶部栏：项目与风险等级标识 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 8,
            marginBottom: 12,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="badge badge-amber">
              <Sparkles size={12} /> 交互式决策唤醒 ({pendingCount})
            </span>
            {decision.project_name && (
              <span className="badge badge-blue">{decision.project_name}</span>
            )}
            {isHighRisk ? (
              <span className="badge badge-red">
                <ShieldAlert size={12} /> 高风险门禁 · 必须人工确认（禁自动通过）
              </span>
            ) : (
              <span className="badge badge-gray">
                风险等级: {decision.risk_level}
              </span>
            )}
          </div>
          <button
            type="button"
            className="btn"
            style={{ minHeight: 32, padding: '0 10px' }}
            title="最小化并保留当前答案 (Esc)"
            onClick={() => {
              setPausedByInteraction(true);
              setMinimized(true);
            }}
          >
            <Minimize2 size={14} /> 稍后处理
          </button>
        </div>

        {/* 标题与上下文 */}
        <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 6 }}>
          {decision.title}
        </h3>
        {decision.context_summary && (
          <p
            style={{
              color: 'var(--text-secondary)',
              fontSize: 13,
              marginBottom: 14,
              background: 'var(--bg-base)',
              padding: '10px 12px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            {decision.context_summary}
          </p>
        )}

        {/* 45秒动态倒计时与交互暂停条 */}
        {canAutoSubmitRecommended ? (
          <div
            style={{
              marginBottom: 14,
              padding: '10px 12px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontSize: 12,
                marginBottom: 6,
              }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Clock size={14} color="#3b82f6" />
                {pausedByInteraction
                  ? '检测到您的交互操作，倒计时已自动暂停'
                  : `${remainingSec} 秒后若无操作将自动采纳推荐方案`}
              </span>
              <button
                type="button"
                className="btn"
                style={{ minHeight: 26, padding: '0 8px', fontSize: 11 }}
                onClick={(e) => {
                  e.stopPropagation();
                  setPausedByInteraction((p) => !p);
                }}
              >
                {pausedByInteraction ? (
                  <>
                    <PlayCircle size={12} /> 恢复倒计时
                  </>
                ) : (
                  <>
                    <PauseCircle size={12} /> 暂停
                  </>
                )}
              </button>
            </div>
            <div
              style={{
                height: 5,
                background: 'var(--bg-base)',
                borderRadius: 999,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: '100%',
                  background: pausedByInteraction
                    ? 'var(--status-warning)'
                    : 'var(--accent-blue)',
                  transition: 'width 0.3s linear',
                }}
              />
            </div>
          </div>
        ) : (
          <div
            style={{
              marginBottom: 14,
              padding: '8px 12px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--status-danger-soft)',
              border: '1px solid rgba(239,68,68,0.35)',
              fontSize: 12,
              color: '#fca5a5',
            }}
          >
            当前决策策略为人工必审（{decision.timeout_policy}），系统不会在超时后自动提交，请显式确认。
          </div>
        )}

        {/* 选项卡片列表（桌面快捷键 1/2/3 + 移动端 >=54px 触控） */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 14 }}>
          {decision.options.map((opt, idx) => {
            const isSelected = selectedOptionId === opt.option_id;
            const isRec =
              opt.is_recommended ||
              opt.option_id === decision.recommended_option_id;
            return (
              <button
                key={opt.option_id}
                type="button"
                className={`decision-option-card ${isSelected ? 'selected' : ''}`}
                onClick={() => {
                  setPausedByInteraction(true);
                  setSelectedOptionId(opt.option_id);
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    width: '100%',
                    gap: 8,
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 14 }}>
                    <kbd
                      style={{
                        display: 'inline-block',
                        padding: '1px 6px',
                        marginRight: 8,
                        borderRadius: 4,
                        background: 'var(--bg-base)',
                        border: '1px solid var(--border-subtle)',
                        fontFamily: 'var(--font-mono)',
                        fontSize: 11,
                      }}
                    >
                      {idx + 1}
                    </kbd>
                    {opt.label}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {isRec && <span className="badge badge-green">推荐方案</span>}
                    {isSelected && <CheckCircle2 size={16} color="#3b82f6" />}
                  </div>
                </div>
                {opt.description && (
                  <div
                    style={{
                      fontSize: 12,
                      color: 'var(--text-secondary)',
                      paddingLeft: 28,
                    }}
                  >
                    {opt.description}
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* 补充意见输入框 */}
        {decision.allow_custom_input && (
          <div style={{ marginBottom: 14 }}>
            <label
              style={{
                display: 'block',
                fontSize: 12,
                color: 'var(--text-secondary)',
                marginBottom: 6,
              }}
            >
              补充说明或修订指令（可选，输入时自动暂停倒计时）：
            </label>
            <textarea
              className="textarea"
              rows={2}
              placeholder="可输入额外约束条件或具体修改要求..."
              value={customNote}
              onFocus={() => setPausedByInteraction(true)}
              onChange={(e) => {
                setPausedByInteraction(true);
                setCustomNote(e.target.value);
              }}
            />
          </div>
        )}

        {/* 提交失败错误横幅（答案已保留，支持一键重试） */}
        {submitError && (
          <div
            style={{
              marginBottom: 14,
              padding: '10px 12px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--status-danger-soft)',
              border: '1px solid rgba(239,68,68,0.45)',
              color: '#fca5a5',
              fontSize: 12,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 10,
            }}
          >
            <span>提交未成功：{submitError}（您选择的答案已保留）</span>
            <button
              type="button"
              className="btn btn-danger"
              style={{ minHeight: 30, padding: '0 10px' }}
              onClick={() => void handleSubmit('user_manual')}
            >
              <RefreshCw size={13} /> 立即重试
            </button>
          </div>
        )}

        {/* 底部操作区 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 10,
            paddingTop: 10,
            borderTop: '1px solid var(--border-subtle)',
          }}
        >
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            快捷键：数字键 1-{decision.options.length} 选方案 · Enter 确认拍板 · Esc 稍后
          </span>
          <button
            type="button"
            className="btn btn-primary"
            style={{ minHeight: 44, padding: '0 20px' }}
            disabled={submitting}
            onClick={() => void handleSubmit('user_manual')}
          >
            {submitting ? '正在幂等同步...' : '确认拍板 (Enter)'}
          </button>
        </div>
      </div>
    </div>
  );
};

