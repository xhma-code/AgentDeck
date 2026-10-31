import type {
  ArtifactItem,
  DecisionItem,
  EventItem,
  MachineItem,
  McpTokenItem,
  OverviewResponse,
  ProjectItem,
  RuntimeInstanceItem,
  RuntimeProviderItem,
  SessionItem,
  StepItem,
  StepRevisionItem,
  TaskDependencyItem,
  TaskItem,
  TaskRevisionItem,
} from '../types/api';

const TOKEN_STORAGE_KEY = 'agentdeck_device_token';

export function getStoredToken(): string {
  return localStorage.getItem(TOKEN_STORAGE_KEY) || '';
}

export function setStoredToken(token: string): void {
  if (token) {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  }
}

export class ApiError extends Error {
  status: number;
  problem?: string;
  causeDetail?: string;
  fix?: string;

  constructor(status: number, message: string, data?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.problem = typeof data?.problem === 'string' ? data.problem : undefined;
    this.causeDetail = typeof data?.cause === 'string' ? data.cause : undefined;
    this.fix = typeof data?.fix === 'string' ? data.fix : undefined;
  }
}

async function requestJson<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!headers.has('Content-Type') && options.body) {
    headers.set('Content-Type', 'application/json');
  }
  const token = getStoredToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const res = await fetch(path, {
    ...options,
    headers,
    credentials: 'include',
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg =
      data?.problem ||
      data?.error ||
      data?.detail ||
      `请求失败 (HTTP ${res.status})`;
    throw new ApiError(res.status, msg, data);
  }
  return data as T;
}

export const apiClient = {
  async ensurePairingFromUrl(): Promise<void> {
    const params = new URLSearchParams(window.location.search);
    const pairCode = params.get('pair_code');
    if (pairCode) {
      try {
        const res = await requestJson<{ ok: boolean; token: string }>(
          '/api/v1/auth/pair',
          {
            method: 'POST',
            body: JSON.stringify({
              pair_code: pairCode,
              device_name: navigator.userAgent.includes('Mobile')
                ? 'Mobile PWA'
                : 'Desktop Browser',
            }),
          },
        );
        if (res.token) {
          setStoredToken(res.token);
        }
      } finally {
        params.delete('pair_code');
        const nextQuery = params.toString();
        window.history.replaceState(
          {},
          '',
          window.location.pathname + (nextQuery ? `?${nextQuery}` : ''),
        );
      }
    }
  },

  getOverview(): Promise<OverviewResponse> {
    return requestJson('/api/v1/overview');
  },

  getAuthStatus(): Promise<Record<string, unknown>> {
    return requestJson('/api/v1/auth/status');
  },

  listProjects(): Promise<{ ok: boolean; projects: ProjectItem[] }> {
    return requestJson('/api/v1/projects');
  },

  createProject(payload: {
    name: string;
    root_path: string;
    slug?: string;
    description?: string;
    summary_next_action?: string;
  }): Promise<{ ok: boolean; project: ProjectItem }> {
    return requestJson('/api/v1/projects', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  getProjectDetail(projectId: string): Promise<{
    ok: boolean;
    project: ProjectItem;
    tasks: TaskItem[];
    steps: StepItem[];
    dependencies: TaskDependencyItem[];
    sessions: SessionItem[];
    decisions: DecisionItem[];
    artifacts: ArtifactItem[];
    events: EventItem[];
    mcp_tokens: McpTokenItem[];
  }> {
    return requestJson(`/api/v1/projects/${projectId}`);
  },

  issueMcpToken(
    projectId: string,
    tokenName: string,
  ): Promise<{
    ok: boolean;
    token: {
      token_id: string;
      token_name: string;
      token_prefix: string;
      raw_token: string;
      scopes: string[];
    };
  }> {
    return requestJson(`/api/v1/projects/${projectId}/mcp-tokens`, {
      method: 'POST',
      body: JSON.stringify({ token_name: tokenName }),
    });
  },

  revokeMcpToken(projectId: string, tokenId: string): Promise<{ ok: boolean }> {
    return requestJson(`/api/v1/projects/${projectId}/mcp-tokens/${tokenId}`, {
      method: 'DELETE',
    });
  },

  createTask(
    projectId: string,
    payload: {
      title: string;
      goal: string;
      acceptance_criteria: string[];
      parent_task_id?: string | null;
      priority: 'low' | 'medium' | 'high' | 'critical';
    },
  ): Promise<{ ok: boolean; task: TaskItem }> {
    return requestJson(`/api/v1/projects/${projectId}/tasks`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  getTaskDetail(
    projectId: string,
    taskId: string,
  ): Promise<{
    ok: boolean;
    task: TaskItem;
    revisions: TaskRevisionItem[];
    steps: StepItem[];
    step_revisions: Record<string, StepRevisionItem[]>;
    dependencies: TaskDependencyItem[];
    decisions: DecisionItem[];
    artifacts: ArtifactItem[];
    events: EventItem[];
  }> {
    return requestJson(`/api/v1/projects/${projectId}/tasks/${taskId}`);
  },

  reviseTask(
    projectId: string,
    taskId: string,
    payload: {
      title: string;
      goal: string;
      acceptance_criteria: string[];
      change_reason: string;
    },
  ): Promise<{
    ok: boolean;
    task: TaskItem;
    revision: TaskRevisionItem;
    revalidation_impact: {
      impacted_step_ids: string[];
      impacted_task_ids: string[];
      reason: string;
    };
  }> {
    return requestJson(
      `/api/v1/projects/${projectId}/tasks/${taskId}/revisions`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
  },

  updateTaskStatus(
    projectId: string,
    taskId: string,
    payload: {
      platform_status?: string;
      clear_revalidation?: boolean;
    },
  ): Promise<{ ok: boolean; task: TaskItem }> {
    return requestJson(`/api/v1/projects/${projectId}/tasks/${taskId}/status`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  },

  addTaskDependency(
    projectId: string,
    taskId: string,
    dependsOnTaskId: string,
  ): Promise<{ ok: boolean; dependency: TaskDependencyItem }> {
    return requestJson(
      `/api/v1/projects/${projectId}/tasks/${taskId}/dependencies`,
      {
        method: 'POST',
        body: JSON.stringify({ depends_on_task_id: dependsOnTaskId }),
      },
    );
  },

  createStep(
    projectId: string,
    taskId: string,
    payload: { title: string; instruction: string },
  ): Promise<{ ok: boolean; step: StepItem }> {
    return requestJson(`/api/v1/projects/${projectId}/tasks/${taskId}/steps`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  reviseStep(
    projectId: string,
    stepId: string,
    payload: { title: string; instruction: string; change_reason: string },
  ): Promise<{ ok: boolean; step: StepItem }> {
    return requestJson(
      `/api/v1/projects/${projectId}/steps/${stepId}/revisions`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
  },

  updateStepStatus(
    projectId: string,
    stepId: string,
    payload: { platform_status?: string; clear_revalidation?: boolean },
  ): Promise<{ ok: boolean; step: StepItem }> {
    return requestJson(`/api/v1/projects/${projectId}/steps/${stepId}/status`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  },

  listSessions(projectId?: string): Promise<{ ok: boolean; sessions: SessionItem[] }> {
    const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    return requestJson(`/api/v1/sessions${q}`);
  },

  createSession(
    projectId: string,
    payload: {
      title: string;
      task_id?: string | null;
      runtime_instance_id?: string | null;
      source_channel?: string;
    },
  ): Promise<{ ok: boolean; session: SessionItem }> {
    return requestJson(`/api/v1/projects/${projectId}/sessions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  getSessionDetail(sessionId: string): Promise<{
    ok: boolean;
    session: SessionItem;
    project: ProjectItem;
    task: TaskItem | null;
    steps: StepItem[];
    runtime_instance: RuntimeInstanceItem | null;
    external_refs: Array<{
      id: string;
      remote_entry_kind: string;
      remote_entry_url: string;
      external_session_id: string;
      link_status: string;
    }>;
    decisions: DecisionItem[];
    artifacts: ArtifactItem[];
    timeline_events: EventItem[];
  }> {
    return requestJson(`/api/v1/sessions/${sessionId}`);
  },

  bindExternalSessionRef(
    projectId: string,
    sessionId: string,
    payload: {
      runtime_instance_id: string;
      external_session_id: string;
      remote_entry_kind: string;
      remote_entry_url: string;
    },
  ): Promise<{ ok: boolean }> {
    return requestJson(
      `/api/v1/projects/${projectId}/sessions/${sessionId}/external-refs`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    );
  },

  listDecisions(params?: {
    project_id?: string;
    status?: string;
  }): Promise<{ ok: boolean; decisions: DecisionItem[] }> {
    const search = new URLSearchParams();
    if (params?.project_id) search.set('project_id', params.project_id);
    if (params?.status) search.set('status', params.status);
    const qs = search.toString();
    return requestJson(`/api/v1/decisions${qs ? `?${qs}` : ''}`);
  },

  createDecision(
    projectId: string,
    payload: Record<string, unknown>,
  ): Promise<{ ok: boolean; decision: DecisionItem }> {
    return requestJson(`/api/v1/projects/${projectId}/decisions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /**
   * 指数退避幂等提交决策答案（800ms -> 1600ms -> 3200ms）：
   * - 网络抖动或 5xx 时使用同一 Idempotency-Key 自动重试最多 3 次；
   * - 遇到 409 Conflict 时视为已被其他端或幂等处理完成，安全结束。
   */
  async resolveDecisionWithRetry(
    decisionId: string,
    payload: {
      chosen_option_id: string | null;
      custom_note: string;
      resolved_by: 'user_manual' | 'timeout_auto_recommended';
      idempotency_key: string;
    },
  ): Promise<{
    ok: boolean;
    is_idempotent_replay?: boolean;
    already_resolved_conflict?: boolean;
    decision?: DecisionItem;
  }> {
    const delays = [800, 1600, 3200];
    let lastErr: unknown = null;

    for (let attempt = 0; attempt <= delays.length; attempt++) {
      try {
        return await requestJson(`/api/v1/decisions/${decisionId}/resolve`, {
          method: 'POST',
          headers: {
            'Idempotency-Key': payload.idempotency_key,
          },
          body: JSON.stringify(payload),
        });
      } catch (err) {
        lastErr = err;
        if (err instanceof ApiError) {
          if (err.status === 409) {
            return { ok: true, already_resolved_conflict: true };
          }
          if (err.status >= 400 && err.status < 500) {
            throw err;
          }
        }
        if (attempt < delays.length) {
          await new Promise((r) => setTimeout(r, delays[attempt]));
        }
      }
    }
    throw lastErr;
  },

  listRuntimeProviders(): Promise<{
    ok: boolean;
    providers: RuntimeProviderItem[];
  }> {
    return requestJson('/api/v1/runtimes/providers');
  },

  listMachines(): Promise<{ ok: boolean; machines: MachineItem[] }> {
    return requestJson('/api/v1/machines');
  },

  listRuntimeInstances(): Promise<{
    ok: boolean;
    instances: RuntimeInstanceItem[];
  }> {
    return requestJson('/api/v1/runtimes/instances');
  },

  registerRuntimeInstance(payload: {
    provider_key: string;
    machine_id: string;
    display_name?: string;
    runtime_version?: string;
    control_level?: string;
    health_status?: string;
    model_endpoint_descriptor?: {
      provider_label: string;
      model_label: string;
      endpoint_kind: string;
    } | null;
  }): Promise<{ ok: boolean; instance: RuntimeInstanceItem }> {
    return requestJson('/api/v1/runtimes/instances', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  triggerBackup(): Promise<{
    ok: boolean;
    backup_path: string;
    filename: string;
  }> {
    return requestJson('/api/v1/system/backup?label=ui_manual', {
      method: 'POST',
    });
  },
};

