import type {
  ArtifactItem,
  CapabilityManifestItem,
  DecisionItem,
  EventItem,
  ExternalSessionRefItem,
  MachineItem,
  McpTokenItem,
  OverviewResponse,
  ProjectItem,
  RemoteRegistryEntryItem,
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

/**
 * 统一交互式 Decision 提交重试策略（与后端 DECISION_RETRY_POLICY 严格一致）：
 * - 第 1 次重试等待 1000ms (1s)
 * - 第 2 次重试等待 2000ms (2s)
 * - 第 3 次重试等待 4000ms (4s)
 */
export const DECISION_RETRY_POLICY = {
  maxRetries: 3,
  backoffMs: [1000, 2000, 4000] as const,
  retryableHttpStatuses: new Set([408, 429, 500, 502, 503, 504]),
  finalFailureState: 'draft_preserved_manual_retry',
};

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

  rotateMcpToken(
    projectId: string,
    tokenId: string,
  ): Promise<{
    ok: boolean;
    revoked_token_id: string;
    token: {
      token_id: string;
      token_name: string;
      token_prefix: string;
      raw_token: string;
      scopes: string[];
    };
  }> {
    return requestJson(
      `/api/v1/projects/${projectId}/mcp-tokens/${tokenId}/rotate`,
      {
        method: 'POST',
        body: JSON.stringify({}),
      },
    );
  },

  getMcpToolsDiagnostic(): Promise<{
    ok: boolean;
    target_protocol_version: string;
    supported_protocol_versions: string[];
    available_scopes: string[];
    tools: Array<{
      name: string;
      title: string;
      description: string;
      annotations?: { requiredScope?: string };
    }>;
  }> {
    return requestJson('/api/v1/mcp/tools');
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
    external_refs: ExternalSessionRefItem[];
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
      link_status?: string;
    },
  ): Promise<{ ok: boolean; external_ref: ExternalSessionRefItem }> {
    return requestJson(
      `/api/v1/projects/${projectId}/sessions/${sessionId}/external-refs`,
      {
        method: 'POST',
        headers: { 'X-Project-Scope': projectId },
        body: JSON.stringify(payload),
      },
    );
  },

  updateExternalSessionRef(
    projectId: string,
    sessionId: string,
    refId: string,
    payload: {
      runtime_instance_id?: string;
      external_session_id?: string;
      remote_entry_kind?: string;
      remote_entry_url?: string;
      link_status?: string;
    },
  ): Promise<{ ok: boolean; external_ref: ExternalSessionRefItem }> {
    return requestJson(
      `/api/v1/projects/${projectId}/sessions/${sessionId}/external-refs/${refId}`,
      {
        method: 'PATCH',
        headers: { 'X-Project-Scope': projectId },
        body: JSON.stringify(payload),
      },
    );
  },

  updateSessionConnectionStatus(
    projectId: string,
    sessionId: string,
    payload: {
      connection_status: 'connected' | 'disconnected' | 'unknown';
      sync_link_status?: 'active' | 'stale' | 'disconnected' | 'unknown';
      expected_status_seq?: number;
    },
  ): Promise<{ ok: boolean; session: SessionItem }> {
    return requestJson(
      `/api/v1/projects/${projectId}/sessions/${sessionId}/connection`,
      {
        method: 'PATCH',
        headers: { 'X-Project-Scope': projectId },
        body: JSON.stringify(payload),
      },
    );
  },

  listProjectRemoteRegistry(
    projectId: string,
  ): Promise<{
    ok: boolean;
    project_id: string;
    remotes: RemoteRegistryEntryItem[];
  }> {
    return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/remotes`, {
      headers: { 'X-Project-Scope': projectId },
    });
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
   * 指数退避幂等提交决策答案（1000ms -> 2000ms -> 4000ms）：
   * - 网络抖动或可重试服务端错误（408/429/5xx）时使用同一 Idempotency-Key 自动重试最多 3 次；
   * - 遇到 409 Conflict 时视为已被其他端或幂等处理完成，安全结束；
   * - 最终失败时抛出错误，由调用方保留本地草稿供人工一键重试。
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
    const delays = DECISION_RETRY_POLICY.backoffMs;
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
          if (!DECISION_RETRY_POLICY.retryableHttpStatuses.has(err.status)) {
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

  registerMachine(payload: {
    display_name: string;
    os_type?: string;
    hostname_alias?: string;
    connection_kind?: string;
    health_status?: string;
  }): Promise<{ ok: boolean; machine: MachineItem }> {
    return requestJson('/api/v1/machines', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  updateMachine(
    machineId: string,
    payload: {
      display_name?: string;
      hostname_alias?: string;
      connection_kind?: string;
      health_status?: string;
    },
  ): Promise<{ ok: boolean; machine: MachineItem }> {
    return requestJson(`/api/v1/machines/${machineId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
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
    connector_version?: string;
    control_level?: string;
    health_status?: string;
    capabilities?: CapabilityManifestItem[];
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

  updateRuntimeInstance(
    instanceId: string,
    payload: {
      display_name?: string;
      runtime_version?: string;
      connector_version?: string;
      control_level?: string;
      health_status?: string;
      capabilities?: CapabilityManifestItem[];
    },
  ): Promise<{ ok: boolean; instance: RuntimeInstanceItem }> {
    return requestJson(`/api/v1/runtimes/instances/${instanceId}`, {
      method: 'PATCH',
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

