import React from 'react';

const STATUS_STYLE_MAP: Record<string, { label: string; cls: string }> = {
  active: { label: '活跃中', cls: 'badge-green' },
  online: { label: '在线', cls: 'badge-green' },
  connected: { label: '已连接', cls: 'badge-green' },
  completed: { label: '已完成', cls: 'badge-green' },
  succeeded: { label: '已成功', cls: 'badge-green' },
  resolved: { label: '已裁决', cls: 'badge-green' },
  supported: { label: '已支持', cls: 'badge-green' },

  in_progress: { label: '进行中', cls: 'badge-blue' },
  running: { label: '运行中', cls: 'badge-blue' },
  ready: { label: '就绪', cls: 'badge-blue' },

  waiting_interaction: { label: '等待交互拍板', cls: 'badge-amber' },
  waiting_decision: { label: '等待决策', cls: 'badge-amber' },
  waiting_input: { label: '等待输入', cls: 'badge-amber' },
  pending: { label: '待处理', cls: 'badge-amber' },
  needs_revalidation: { label: '待人工重验', cls: 'badge-purple' },
  unverified: { label: '未验证', cls: 'badge-amber' },

  blocked: { label: '已阻塞', cls: 'badge-red' },
  failed: { label: '失败', cls: 'badge-red' },
  offline: { label: '离线', cls: 'badge-red' },
  unsupported: { label: '不支持', cls: 'badge-red' },

  disconnected: { label: 'Remote 已断开(非失败)', cls: 'badge-gray' },
  draft: { label: '草稿', cls: 'badge-gray' },
  idle: { label: '空闲', cls: 'badge-gray' },
  paused: { label: '已暂停', cls: 'badge-gray' },
  archived: { label: '已归档', cls: 'badge-gray' },
  unknown: { label: '未知', cls: 'badge-gray' },
};

export const StatusBadge: React.FC<{
  status: string;
  prefix?: string;
}> = ({ status, prefix }) => {
  const item = STATUS_STYLE_MAP[status] || {
    label: status || '未知',
    cls: 'badge-gray',
  };
  return (
    <span className={`badge ${item.cls}`}>
      {prefix ? `${prefix}: ${item.label}` : item.label}
    </span>
  );
};

