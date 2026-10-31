import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertCircle,
  CheckCircle,
  Cpu,
  ExternalLink,
  FolderGit2,
  GitBranch,
  KeyRound,
  Layers,
  ListTodo,
  Plus,
  RefreshCw,
  ShieldCheck,
  TerminalSquare,
} from 'lucide-react';
import { apiClient } from './api/client';
import { InteractiveDecisionModal } from './components/InteractiveDecisionModal';
import { StatusBadge } from './components/StatusBadge';
import type {
  ArtifactItem,
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
} from './types/api';

type NavTab = 'overview' | 'projects' | 'sessions' | 'decisions' | 'runtimes';

const CONTROL_LEVEL_LABELS: Record<string, string> = {
  L0: 'L0 · 官方 Remote 入口',
  L1: 'L1 · Runtime 注册、状态与能力信息管理',
  L2: 'L2 · 程序化控制',
  L3: 'L3 · 审批与恢复能力',
};

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<NavTab>('overview');
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string>('');
  const [toastMsg, setToastMsg] = useState<string>('');

  // 项目详情与规划状态 (P06 / P07 / P08)
  const [projectTasks, setProjectTasks] = useState<TaskItem[]>([]);
  const [projectSteps, setProjectSteps] = useState<StepItem[]>([]);
  const [projectDeps, setProjectDeps] = useState<TaskDependencyItem[]>([]);
  const [projectTokens, setProjectTokens] = useState<McpTokenItem[]>([]);
  const [newlyIssuedRawToken, setNewlyIssuedRawToken] = useState<string>('');

  // 选中的任务详情与修订历史 (P08)
  const [selectedTaskId, setSelectedTaskId] = useState<string>('');
  const [taskRevisions, setTaskRevisions] = useState<TaskRevisionItem[]>([]);
  const [stepRevisionsMap, setStepRevisionsMap] = useState<
    Record<string, StepRevisionItem[]>
  >({});
  const [taskArtifacts, setTaskArtifacts] = useState<ArtifactItem[]>([]);

  // 选中的会话详情 (P09 / P10 v1.3)
  const [selectedSessionId, setSelectedSessionId] = useState<string>('');
  const [sessionDetail, setSessionDetail] = useState<{
    session: SessionItem;
    project: ProjectItem;
    task: TaskItem | null;
    steps: StepItem[];
    runtime_instance: RuntimeInstanceItem | null;
    external_refs: ExternalSessionRefItem[];
    decisions: DecisionItem[];
    artifacts: ArtifactItem[];
    timeline_events: EventItem[];
  } | null>(null);

  // Runtime、Remote Registry 与决策列表状态 (P02 / P03 / P14)
  const [allDecisions, setAllDecisions] = useState<DecisionItem[]>([]);
  const [providers, setProviders] = useState<RuntimeProviderItem[]>([]);
  const [machines, setMachines] = useState<MachineItem[]>([]);
  const [runtimeInstances, setRuntimeInstances] = useState<
    RuntimeInstanceItem[]
  >([]);
  const [projectRemotes, setProjectRemotes] = useState<
    RemoteRegistryEntryItem[]
  >([]);

  // 表单弹窗开关与状态
  const [showNewProjectModal, setShowNewProjectModal] =
    useState<boolean>(false);
  const [newProjName, setNewProjName] = useState<string>('');
  const [newProjRoot, setNewProjRoot] = useState<string>('');
  const [newProjDesc, setNewProjDesc] = useState<string>('');
  const [newProjNextAction, setNewProjNextAction] = useState<string>('');

  const [newTaskTitle, setNewTaskTitle] = useState<string>('');
  const [newTaskGoal, setNewTaskGoal] = useState<string>('');
  const [newTaskCriteria, setNewTaskCriteria] = useState<string>('');
  const [newTaskParentId, setNewTaskParentId] = useState<string>('');
  const [newTaskPriority, setNewTaskPriority] = useState<
    'low' | 'medium' | 'high' | 'critical'
  >('medium');

  const [revTitle, setRevTitle] = useState<string>('');
  const [revGoal, setRevGoal] = useState<string>('');
  const [revCriteria, setRevCriteria] = useState<string>('');
  const [revReason, setRevReason] = useState<string>('');

  const [newStepTitle, setNewStepTitle] = useState<string>('');
  const [newStepInstr, setNewStepInstr] = useState<string>('');
  const [depPrereqTaskId, setDepPrereqTaskId] = useState<string>('');

  const [newSessTitle, setNewSessTitle] = useState<string>('');
  const [newSessTaskId, setNewSessTaskId] = useState<string>('');
  const [newSessRuntimeId, setNewSessRuntimeId] = useState<string>('');

  const [extRefRuntimeId, setExtRefRuntimeId] = useState<string>('');
  const [extRefNativeId, setExtRefNativeId] = useState<string>('');
  const [extRefKind, setExtRefKind] = useState<string>('claude_remote_control');
  const [extRefUrl, setExtRefUrl] = useState<string>('');

  const [newInstProvider, setNewInstProvider] = useState<string>('claude_code');
  const [newInstMachineId, setNewInstMachineId] =
    useState<string>('local-machine');
  const [newInstName, setNewInstName] = useState<string>('');
  const [newInstVersion, setNewInstVersion] = useState<string>('1.x');
  const [newInstLevel, setNewInstLevel] = useState<'L0' | 'L1'>('L0');
  const [newInstProviderLabel, setNewInstProviderLabel] = useState<string>('');
  const [newInstModelLabel, setNewInstModelLabel] = useState<string>('');

  const [newMachineName, setNewMachineName] = useState<string>('');
  const [newMachineOs, setNewMachineOs] = useState<string>('windows');
  const [newMachineAlias, setNewMachineAlias] = useState<string>('');
  const [newMachineKind, setNewMachineKind] = useState<string>('local');

  const notify = useCallback((msg: string) => {
    setToastMsg(msg);
    window.setTimeout(() => setToastMsg(''), 3500);
  }, []);

  const refreshOverview = useCallback(async () => {
    try {
      const ov = await apiClient.getOverview();
      setOverview(ov);
      setErrorMsg('');
      if (!selectedProjectId && ov.projects.length > 0) {
        setSelectedProjectId(ov.projects[0].id);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '加载工作总览失败');
    } finally {
      setLoading(false);
    }
  }, [selectedProjectId]);

  const loadProjectContext = useCallback(async (projectId: string) => {
    if (!projectId) return;
    try {
      const [detail, remotesRes] = await Promise.all([
        apiClient.getProjectDetail(projectId),
        apiClient.listProjectRemoteRegistry(projectId),
      ]);
      setProjectTasks(detail.tasks);
      setProjectSteps(detail.steps);
      setProjectDeps(detail.dependencies);
      setProjectTokens(detail.mcp_tokens);
      setProjectRemotes(remotesRes.remotes);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '加载项目详情失败');
    }
  }, []);

  const loadTaskContext = useCallback(
    async (projectId: string, taskId: string) => {
      if (!projectId || !taskId) return;
      try {
        const td = await apiClient.getTaskDetail(projectId, taskId);
        setTaskRevisions(td.revisions);
        setStepRevisionsMap(td.step_revisions);
        setTaskArtifacts(td.artifacts);
        setRevTitle(td.task.title);
        setRevGoal(td.task.goal);
        setRevCriteria(td.task.acceptance_criteria.join('\n'));
        setRevReason('');
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : '加载任务详情失败');
      }
    },
    [],
  );

  const loadSessionContext = useCallback(async (sessionId: string) => {
    if (!sessionId) return;
    try {
      const sd = await apiClient.getSessionDetail(sessionId);
      setSessionDetail(sd);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '加载会话详情失败');
    }
  }, []);

  const loadRuntimesAndDecisions = useCallback(async () => {
    try {
      const [decsRes, provRes, machRes, instRes] = await Promise.all([
        apiClient.listDecisions(),
        apiClient.listRuntimeProviders(),
        apiClient.listMachines(),
        apiClient.listRuntimeInstances(),
      ]);
      setAllDecisions(decsRes.decisions);
      setProviders(provRes.providers);
      setMachines(machRes.machines);
      setRuntimeInstances(instRes.instances);
    } catch {
      // 忽略静默轮询异常
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await apiClient.ensurePairingFromUrl();
      await refreshOverview();
      await loadRuntimesAndDecisions();
    })();
  }, [loadRuntimesAndDecisions, refreshOverview]);

  // 定时轮询 Overview 与全局待确认 Decision，保证跨页面自动唤醒弹窗
  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshOverview();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshOverview]);

  useEffect(() => {
    if (selectedProjectId) {
      void loadProjectContext(selectedProjectId);
    }
  }, [loadProjectContext, selectedProjectId]);

  useEffect(() => {
    if (selectedProjectId && selectedTaskId) {
      void loadTaskContext(selectedProjectId, selectedTaskId);
    }
  }, [loadTaskContext, selectedProjectId, selectedTaskId]);

  useEffect(() => {
    if (selectedSessionId) {
      void loadSessionContext(selectedSessionId);
    }
  }, [loadSessionContext, selectedSessionId]);

  const activePendingDecision: DecisionItem | null = useMemo(() => {
    const list = overview?.pending_decisions || [];
    return list.length > 0 ? list[0] : null;
  }, [overview]);

  const currentProject: ProjectItem | undefined = useMemo(() => {
    return overview?.projects.find((p) => p.id === selectedProjectId);
  }, [overview, selectedProjectId]);

  const selectedTask: TaskItem | undefined = useMemo(() => {
    return projectTasks.find((t) => t.id === selectedTaskId);
  }, [projectTasks, selectedTaskId]);

  // -------------------- 交互操作处理函数 --------------------

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await apiClient.createProject({
        name: newProjName.trim(),
        root_path: newProjRoot.trim(),
        description: newProjDesc.trim(),
        summary_next_action: newProjNextAction.trim(),
      });
      setShowNewProjectModal(false);
      setNewProjName('');
      setNewProjRoot('');
      setNewProjDesc('');
      setNewProjNextAction('');
      setSelectedProjectId(res.project.id);
      await refreshOverview();
      await loadProjectContext(res.project.id);
      setActiveTab('projects');
      notify(`已创建项目：${res.project.name}`);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '创建项目失败');
    }
  };

  const handleCreateTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProjectId || !newTaskTitle.trim()) return;
    try {
      const criteria = newTaskCriteria
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await apiClient.createTask(selectedProjectId, {
        title: newTaskTitle.trim(),
        goal: newTaskGoal.trim(),
        acceptance_criteria: criteria,
        parent_task_id: newTaskParentId || null,
        priority: newTaskPriority,
      });
      setNewTaskTitle('');
      setNewTaskGoal('');
      setNewTaskCriteria('');
      setSelectedTaskId(res.task.id);
      await loadProjectContext(selectedProjectId);
      await refreshOverview();
      notify(`已创建任务：${res.task.title} (r1)`);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '创建任务失败');
    }
  };

  const handleReviseTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProjectId || !selectedTaskId) return;
    try {
      const criteria = revCriteria
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await apiClient.reviseTask(selectedProjectId, selectedTaskId, {
        title: revTitle.trim(),
        goal: revGoal.trim(),
        acceptance_criteria: criteria,
        change_reason: revReason.trim() || '更新任务目标与验收标准',
      });
      await loadProjectContext(selectedProjectId);
      await loadTaskContext(selectedProjectId, selectedTaskId);
      notify(
        `已生成修订版 r${res.revision.revision_no}（受影响步骤 ${res.revalidation_impact.impacted_step_ids.length} 个，下游任务 ${res.revalidation_impact.impacted_task_ids.length} 个已标为待重验）`,
      );
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '修订任务失败');
    }
  };

  const handleCreateStep = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProjectId || !selectedTaskId || !newStepTitle.trim()) return;
    try {
      await apiClient.createStep(selectedProjectId, selectedTaskId, {
        title: newStepTitle.trim(),
        instruction: newStepInstr.trim(),
      });
      setNewStepTitle('');
      setNewStepInstr('');
      await loadProjectContext(selectedProjectId);
      await loadTaskContext(selectedProjectId, selectedTaskId);
      notify('已追加任务步骤 (r1)');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '添加步骤失败');
    }
  };

  const handleAddDependency = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProjectId || !selectedTaskId || !depPrereqTaskId) return;
    try {
      await apiClient.addTaskDependency(
        selectedProjectId,
        selectedTaskId,
        depPrereqTaskId,
      );
      setDepPrereqTaskId('');
      await loadProjectContext(selectedProjectId);
      notify('已添加同项目前置依赖（已通过 DAG 无环校验）');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '添加依赖失败');
    }
  };

  const handleIssueMcpToken = async () => {
    if (!selectedProjectId) return;
    try {
      const res = await apiClient.issueMcpToken(
        selectedProjectId,
        'claude-code-mcp',
      );
      setNewlyIssuedRawToken(res.token.raw_token);
      await loadProjectContext(selectedProjectId);
      notify('已签发项目专属 MCP Token（明文仅展示一次，请妥善复制）');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '签发 MCP Token 失败');
    }
  };

  const handleRotateMcpToken = async (tokenId: string) => {
    if (!selectedProjectId) return;
    try {
      const res = await apiClient.rotateMcpToken(selectedProjectId, tokenId);
      setNewlyIssuedRawToken(res.token.raw_token);
      await loadProjectContext(selectedProjectId);
      notify('已原子轮换 MCP Token（旧令牌已立即吊销，请复制新令牌）');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '轮换 MCP Token 失败');
    }
  };

  const handleTriggerDemoDecision = async () => {
    if (!selectedProjectId) return;
    try {
      await apiClient.createDecision(selectedProjectId, {
        title: '确认任务架构变更与验收门禁',
        context_summary:
          'Agent 在执行过程中发现两套可选实现路径，请拍板确认后续方向（支持数字键 1/2/Enter 与 45s 倒计时暂停）。',
        decision_type: 'interactive_question',
        risk_level: 'normal',
        timeout_policy: 'auto_submit_recommended',
        timeout_sec: 45,
        task_id: selectedTaskId || null,
        session_id: selectedSessionId || null,
        options: [
          {
            option_id: 'opt_1',
            label: '方案 A：采用 SQLAlchemy 2.x 工作单元原子提交（推荐）',
            description: '保证业务写操作与 events 表处于同一 SQLite 事务',
            is_recommended: true,
          },
          {
            option_id: 'opt_2',
            label: '方案 B：仅内存态通知，延后落盘',
            description: '断线重连时可能丢失审计轨迹',
            is_recommended: false,
          },
        ],
      });
      await refreshOverview();
      await loadRuntimesAndDecisions();
      notify('已发起交互式 Decision，全局决策弹窗已唤醒');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '创建决策失败');
    }
  };

  const handleCreateSession = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProjectId || !newSessTitle.trim()) return;
    try {
      const res = await apiClient.createSession(selectedProjectId, {
        title: newSessTitle.trim(),
        task_id: newSessTaskId || null,
        runtime_instance_id: newSessRuntimeId || null,
        source_channel: 'remote_link',
      });
      setNewSessTitle('');
      setSelectedSessionId(res.session.id);
      await refreshOverview();
      await loadSessionContext(res.session.id);
      notify(`已登记协同会话：${res.session.title}`);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '创建会话失败');
    }
  };

  const handleBindExternalRef = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!sessionDetail || !extRefRuntimeId) return;
    try {
      await apiClient.bindExternalSessionRef(
        sessionDetail.session.project_id,
        sessionDetail.session.id,
        {
          runtime_instance_id: extRefRuntimeId,
          external_session_id:
            extRefNativeId.trim() || `ext-${Date.now().toString().slice(-6)}`,
          remote_entry_kind: extRefKind,
          remote_entry_url: extRefUrl.trim(),
        },
      );
      setExtRefUrl('');
      setExtRefNativeId('');
      await loadSessionContext(sessionDetail.session.id);
      if (selectedProjectId) await loadProjectContext(selectedProjectId);
      notify('已绑定外部原生会话与脱敏官方 Remote 入口');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '绑定官方 Remote 入口失败');
    }
  };

  const handleUpdateConnectionStatus = async (
    nextStatus: 'connected' | 'disconnected' | 'unknown',
  ) => {
    if (!sessionDetail) return;
    try {
      await apiClient.updateSessionConnectionStatus(
        sessionDetail.session.project_id,
        sessionDetail.session.id,
        {
          connection_status: nextStatus,
          sync_link_status:
            nextStatus === 'connected'
              ? 'active'
              : nextStatus === 'disconnected'
                ? 'disconnected'
                : 'unknown',
        },
      );
      await loadSessionContext(sessionDetail.session.id);
      if (selectedProjectId) await loadProjectContext(selectedProjectId);
      await refreshOverview();
      notify(`已更新远程连接状态为 ${nextStatus}（平台任务与会话执行状态保持不变）`);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '更新连接状态失败');
    }
  };

  const handleUpdateExternalRefStatus = async (
    refId: string,
    nextLinkStatus: 'active' | 'stale' | 'disconnected',
  ) => {
    if (!sessionDetail) return;
    try {
      await apiClient.updateExternalSessionRef(
        sessionDetail.session.project_id,
        sessionDetail.session.id,
        refId,
        { link_status: nextLinkStatus },
      );
      await loadSessionContext(sessionDetail.session.id);
      if (selectedProjectId) await loadProjectContext(selectedProjectId);
      notify(`已更新外部会话链接状态为 ${nextLinkStatus}`);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '更新链接状态失败');
    }
  };

  const handleRegisterMachine = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newMachineName.trim()) return;
    try {
      await apiClient.registerMachine({
        display_name: newMachineName.trim(),
        os_type: newMachineOs,
        hostname_alias: newMachineAlias.trim(),
        connection_kind: newMachineKind,
        health_status: 'online',
      });
      setNewMachineName('');
      setNewMachineAlias('');
      await loadRuntimesAndDecisions();
      notify('已登记宿主机器 (Machine)');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '登记宿主机器失败');
    }
  };

  const handleToggleMachineHealth = async (
    machineId: string,
    nextHealth: 'online' | 'offline' | 'degraded',
  ) => {
    try {
      await apiClient.updateMachine(machineId, { health_status: nextHealth });
      await loadRuntimesAndDecisions();
      notify(`宿主机器状态已更新为 ${nextHealth}`);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '更新机器状态失败');
    }
  };

  const handleRegisterRuntimeInstance = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const descriptor =
        newInstProviderLabel.trim() || newInstModelLabel.trim()
          ? {
              provider_label: newInstProviderLabel.trim(),
              model_label: newInstModelLabel.trim(),
              endpoint_kind: 'compatible_api',
            }
          : null;
      await apiClient.registerRuntimeInstance({
        provider_key: newInstProvider,
        machine_id: newInstMachineId || 'local-machine',
        display_name: newInstName.trim() || undefined,
        runtime_version: newInstVersion.trim() || '1.x',
        control_level: newInstLevel,
        health_status: 'online',
        model_endpoint_descriptor: descriptor,
      });
      setNewInstName('');
      setNewInstProviderLabel('');
      setNewInstModelLabel('');
      await loadRuntimesAndDecisions();
      await refreshOverview();
      notify('已登记 Runtime 实例');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '登记 Runtime 实例失败');
    }
  };

  return (
    <div className="app-shell">
      {/* 左侧桌面导航栏 */}
      <aside className="sidebar">
        <div className="brand-header">
          <FolderGit2 size={24} color="#3b82f6" />
          <div>
            <div className="brand-title">AgentDeck</div>
            <div className="brand-sub">v1.3 多项目研发编排驾驶舱</div>
          </div>
        </div>

        <nav className="nav-list">
          <button
            type="button"
            className={`nav-item ${activeTab === 'overview' ? 'active' : ''}`}
            onClick={() => setActiveTab('overview')}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Activity size={17} /> P01 工作总览
            </span>
          </button>

          <button
            type="button"
            className={`nav-item ${activeTab === 'projects' ? 'active' : ''}`}
            onClick={() => setActiveTab('projects')}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <ListTodo size={17} /> P04-P08 项目与任务
            </span>
            <span className="badge badge-blue">
              {overview?.counts.total_projects || 0}
            </span>
          </button>

          <button
            type="button"
            className={`nav-item ${activeTab === 'sessions' ? 'active' : ''}`}
            onClick={() => setActiveTab('sessions')}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <TerminalSquare size={17} /> P09-P10 协同会话
            </span>
            <span className="badge badge-gray">
              {overview?.counts.active_sessions || 0}
            </span>
          </button>

          <button
            type="button"
            className={`nav-item ${activeTab === 'decisions' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('decisions');
              void loadRuntimesAndDecisions();
            }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <AlertCircle size={17} /> P02-P03 待决策中心
            </span>
            {(overview?.counts.pending_decisions || 0) > 0 && (
              <span className="badge badge-amber">
                {overview?.counts.pending_decisions}
              </span>
            )}
          </button>

          <button
            type="button"
            className={`nav-item ${activeTab === 'runtimes' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('runtimes');
              void loadRuntimesAndDecisions();
            }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Cpu size={17} /> P14 Runtime 中心
            </span>
          </button>
        </nav>

        <div
          style={{
            padding: 14,
            borderTop: '1px solid var(--border-subtle)',
            fontSize: 12,
            color: 'var(--text-secondary)',
          }}
        >
          <div style={{ marginBottom: 8, fontWeight: 600 }}>快捷操作</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <button
              type="button"
              className="btn btn-primary"
              style={{ width: '100%' }}
              onClick={() => setShowNewProjectModal(true)}
            >
              <Plus size={15} /> 新建研发项目
            </button>
            <button
              type="button"
              className="btn"
              style={{ width: '100%' }}
              onClick={() => void handleTriggerDemoDecision()}
              disabled={!selectedProjectId}
            >
              发起交互式决策测试
            </button>
          </div>
        </div>
      </aside>

      {/* 主内容区域 */}
      <div className="main-area">
        {/* 顶栏：当前项目切换器与系统状态 */}
        <header className="topbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
              当前项目上下文：
            </span>
            <select
              className="select"
              style={{ width: 260 }}
              value={selectedProjectId}
              onChange={(e) => {
                setSelectedProjectId(e.target.value);
                setSelectedTaskId('');
              }}
            >
              <option value="">-- 请选择或新建项目 --</option>
              {(overview?.projects || []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.slug})
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn"
              onClick={() => setShowNewProjectModal(true)}
            >
              <Plus size={14} /> 新建项目
            </button>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {toastMsg && <span className="badge badge-green">{toastMsg}</span>}
            <button
              type="button"
              className="btn"
              onClick={async () => {
                const res = await apiClient.triggerBackup();
                notify(`在线热备已完成：${res.filename}`);
              }}
            >
              <ShieldCheck size={14} /> SQLite 热备
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => {
                void refreshOverview();
                if (selectedProjectId) void loadProjectContext(selectedProjectId);
              }}
            >
              <RefreshCw size={14} /> 刷新
            </button>
          </div>
        </header>

        {/* 全局错误提示条 */}
        {errorMsg && (
          <div
            style={{
              margin: '12px 20px 0',
              padding: '10px 14px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--status-danger-soft)',
              border: '1px solid rgba(239,68,68,0.45)',
              color: '#fca5a5',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <span>{errorMsg}</span>
            <button
              type="button"
              className="btn"
              style={{ minHeight: 28, padding: '0 8px' }}
              onClick={() => setErrorMsg('')}
            >
              关闭
            </button>
          </div>
        )}

        {/* 主滚动区 */}
        <main className="content-scroll">
          {loading ? (
            <div className="card">正在加载 AgentDeck 编排数据...</div>
          ) : activeTab === 'overview' ? (
            /* ==================== P01 工作总览 ==================== */
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <div className="grid-4">
                <div className="card">
                  <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                    活跃研发项目
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 700, marginTop: 4 }}>
                    {overview?.counts.active_projects || 0}
                  </div>
                </div>
                <div className="card">
                  <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                    待拍板交互决策
                  </div>
                  <div
                    style={{
                      fontSize: 28,
                      fontWeight: 700,
                      marginTop: 4,
                      color:
                        (overview?.counts.pending_decisions || 0) > 0
                          ? 'var(--status-warning)'
                          : 'var(--text-primary)',
                    }}
                  >
                    {overview?.counts.pending_decisions || 0}
                  </div>
                </div>
                <div className="card">
                  <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                    活跃协同会话
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 700, marginTop: 4 }}>
                    {overview?.counts.active_sessions || 0}
                  </div>
                </div>
                <div className="card">
                  <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                    已登记 Runtime 实例
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 700, marginTop: 4 }}>
                    {overview?.counts.runtime_instances || 0}
                  </div>
                </div>
              </div>

              <div className="grid-2">
                {/* 项目列表摘要 */}
                <div className="card">
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      marginBottom: 12,
                    }}
                  >
                    <h3 style={{ fontSize: 16, fontWeight: 700 }}>
                      研发项目矩阵 (Project-First)
                    </h3>
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={() => setShowNewProjectModal(true)}
                    >
                      <Plus size={14} /> 新建项目
                    </button>
                  </div>
                  {(overview?.projects || []).length === 0 ? (
                    <div
                      style={{
                        padding: 24,
                        textAlign: 'center',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      暂无已注册项目，请点击右上角“新建项目”登记本地代码仓库目录。
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {overview?.projects.map((p) => (
                        <div
                          key={p.id}
                          className="card-elevated"
                          style={{
                            cursor: 'pointer',
                            borderColor:
                              p.id === selectedProjectId
                                ? 'var(--accent-blue)'
                                : undefined,
                          }}
                          onClick={() => {
                            setSelectedProjectId(p.id);
                            setActiveTab('projects');
                          }}
                        >
                          <div
                            style={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              alignItems: 'center',
                            }}
                          >
                            <strong>{p.name}</strong>
                            <StatusBadge status={p.status} />
                          </div>
                          <div
                            style={{
                              fontSize: 12,
                              color: 'var(--text-muted)',
                              fontFamily: 'var(--font-mono)',
                              marginTop: 4,
                            }}
                          >
                            {p.root_path}
                          </div>
                          {p.summary_next_action && (
                            <div
                              style={{
                                fontSize: 12,
                                color: 'var(--text-secondary)',
                                marginTop: 6,
                              }}
                            >
                              下一步：{p.summary_next_action}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* 全局有序事件审计流 */}
                <div className="card">
                  <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 12 }}>
                    全局领域事件时间线 (Events)
                  </h3>
                  {(overview?.recent_events || []).length === 0 ? (
                    <div style={{ color: 'var(--text-secondary)' }}>
                      暂无领域事件记录
                    </div>
                  ) : (
                    <div
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 8,
                        maxHeight: 360,
                        overflowY: 'auto',
                      }}
                    >
                      {overview?.recent_events.map((evt) => (
                        <div key={evt.seq_id} className="card-elevated">
                          <div
                            style={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              fontSize: 12,
                            }}
                          >
                            <span className="badge badge-blue">
                              #{evt.seq_id} {evt.event_type}
                            </span>
                            <span style={{ color: 'var(--text-muted)' }}>
                              {evt.created_at}
                            </span>
                          </div>
                          <div
                            style={{
                              fontSize: 12,
                              color: 'var(--text-secondary)',
                              marginTop: 4,
                              fontFamily: 'var(--font-mono)',
                            }}
                          >
                            [{evt.entity_type}] {JSON.stringify(evt.payload)}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : activeTab === 'projects' ? (
            /* ==================== P04-P08 项目概览、任务树与追加式 Revision ==================== */
            !currentProject ? (
              <div className="card">
                请先在顶部下拉框选择一个项目，或点击“新建项目”创建首个研发项目。
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {/* 项目头部概览与 MCP Token 签发区 */}
                <div className="card">
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'flex-start',
                      gap: 12,
                      flexWrap: 'wrap',
                    }}
                  >
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <h2 style={{ fontSize: 20, fontWeight: 700 }}>
                          {currentProject.name}
                        </h2>
                        <StatusBadge status={currentProject.status} />
                        <span className="badge badge-gray">
                          v{currentProject.version}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: 12,
                          color: 'var(--text-muted)',
                          fontFamily: 'var(--font-mono)',
                          marginTop: 4,
                        }}
                      >
                        工作区路径白名单：{currentProject.root_path}
                      </div>
                    </div>

                    <div style={{ display: 'flex', gap: 8 }}>
                      <button
                        type="button"
                        className="btn"
                        onClick={() => void handleIssueMcpToken()}
                      >
                        <KeyRound size={14} /> 签发 Project-scoped MCP Token
                      </button>
                      <button
                        type="button"
                        className="btn btn-warning"
                        onClick={() => void handleTriggerDemoDecision()}
                      >
                        测试交互式决策弹窗
                      </button>
                    </div>
                  </div>

                  {newlyIssuedRawToken && (
                    <div
                      style={{
                        marginTop: 12,
                        padding: 12,
                        borderRadius: 'var(--radius-sm)',
                        background: 'var(--status-success-soft)',
                        border: '1px solid rgba(16,185,129,0.4)',
                      }}
                    >
                      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                        新签发的项目专属 MCP Bearer Token（仅显示一次，数据库仅保存 SHA-256 哈希）：
                      </div>
                      <code
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 13,
                          wordBreak: 'break-all',
                        }}
                      >
                        {newlyIssuedRawToken}
                      </code>
                      <div
                        style={{
                          marginTop: 8,
                          fontSize: 12,
                          color: 'var(--text-secondary)',
                          fontFamily: 'var(--font-mono)',
                        }}
                      >
                        Stdio 接入配置（严禁通过 --token 命令行参数传参，仅限环境变量）：
                        {` { "command": "python", "args": ["-m", "backend.mcp.stdio_server"], "env": { "AGENTDECK_MCP_TOKEN": "${newlyIssuedRawToken}" } } `}
                        | HTTP Streamable 端点：<code>POST /api/v1/mcp</code>（主规范 2026-07-28 / 兼容 2025-11-25）
                      </div>
                    </div>
                  )}

                  {projectTokens.length > 0 && (
                    <div
                      style={{
                        marginTop: 10,
                        display: 'flex',
                        gap: 8,
                        flexWrap: 'wrap',
                      }}
                    >
                      {projectTokens.map((t) => (
                        <span
                          key={t.id}
                          className={`badge ${t.is_revoked ? 'badge-red' : 'badge-gray'}`}
                        >
                          MCP Token: {t.token_prefix}*** ({t.token_name})
                          {!t.is_revoked && (
                            <>
                              <button
                                type="button"
                                style={{
                                  marginLeft: 6,
                                  background: 'transparent',
                                  border: 'none',
                                  color: '#93c5fd',
                                  cursor: 'pointer',
                                }}
                                onClick={() => void handleRotateMcpToken(t.id)}
                              >
                                轮换
                              </button>
                              <button
                                type="button"
                                style={{
                                  marginLeft: 6,
                                  background: 'transparent',
                                  border: 'none',
                                  color: '#fca5a5',
                                  cursor: 'pointer',
                                }}
                                onClick={async () => {
                                  await apiClient.revokeMcpToken(
                                    currentProject.id,
                                    t.id,
                                  );
                                  await loadProjectContext(currentProject.id);
                                }}
                              >
                                吊销
                              </button>
                            </>
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* 分栏：左侧任务树 & 新建任务 (P07) | 右侧任务详情、步骤与追加式修订历史 (P08) */}
                <div className="split-layout">
                  {/* 左侧：任务树 */}
                  <div className="card">
                    <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>
                      P07 任务规划树 ({projectTasks.length})
                    </h3>

                    <div
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 8,
                        marginBottom: 16,
                      }}
                    >
                      {projectTasks.length === 0 ? (
                        <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
                          当前项目尚无任务，请在下方创建任务。
                        </div>
                      ) : (
                        projectTasks.map((t) => (
                          <div
                            key={t.id}
                            className="card-elevated"
                            style={{
                              cursor: 'pointer',
                              marginLeft: t.parent_task_id ? 18 : 0,
                              borderColor:
                                t.id === selectedTaskId
                                  ? 'var(--accent-blue)'
                                  : undefined,
                            }}
                            onClick={() => setSelectedTaskId(t.id)}
                          >
                            <div
                              style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                gap: 6,
                              }}
                            >
                              <span style={{ fontWeight: 600 }}>
                                {t.title}{' '}
                                <span
                                  style={{
                                    fontSize: 11,
                                    color: 'var(--text-muted)',
                                  }}
                                >
                                  (r{t.active_revision_no})
                                </span>
                              </span>
                              <StatusBadge status={t.platform_status} />
                            </div>
                            <div
                              style={{
                                display: 'flex',
                                gap: 6,
                                marginTop: 6,
                                flexWrap: 'wrap',
                              }}
                            >
                              <StatusBadge
                                status={t.agent_reported_status}
                                prefix="Agent自报"
                              />
                              {t.needs_revalidation && (
                                <span className="badge badge-purple">
                                  待人工重验
                                </span>
                              )}
                            </div>
                          </div>
                        ))
                      )}
                    </div>

                    {/* 新建任务表单 */}
                    <form
                      onSubmit={(e) => void handleCreateTask(e)}
                      style={{
                        borderTop: '1px solid var(--border-subtle)',
                        paddingTop: 12,
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 8,
                      }}
                    >
                      <div style={{ fontWeight: 600, fontSize: 13 }}>
                        新建任务（自动生成 TaskRevision r1）
                      </div>
                      <input
                        className="input"
                        placeholder="任务标题（必填）"
                        value={newTaskTitle}
                        onChange={(e) => setNewTaskTitle(e.target.value)}
                        required
                      />
                      <textarea
                        className="textarea"
                        rows={2}
                        placeholder="任务目标说明"
                        value={newTaskGoal}
                        onChange={(e) => setNewTaskGoal(e.target.value)}
                      />
                      <textarea
                        className="textarea"
                        rows={2}
                        placeholder="验收标准（每行一条）"
                        value={newTaskCriteria}
                        onChange={(e) => setNewTaskCriteria(e.target.value)}
                      />
                      <div style={{ display: 'flex', gap: 8 }}>
                        <select
                          className="select"
                          value={newTaskParentId}
                          onChange={(e) => setNewTaskParentId(e.target.value)}
                        >
                          <option value="">根任务（无父任务）</option>
                          {projectTasks.map((pt) => (
                            <option key={pt.id} value={pt.id}>
                              父任务: {pt.title}
                            </option>
                          ))}
                        </select>
                        <select
                          className="select"
                          style={{ width: 120 }}
                          value={newTaskPriority}
                          onChange={(e) =>
                            setNewTaskPriority(
                              e.target.value as
                                | 'low'
                                | 'medium'
                                | 'high'
                                | 'critical',
                            )
                          }
                        >
                          <option value="low">低优</option>
                          <option value="medium">中优</option>
                          <option value="high">高优</option>
                          <option value="critical">紧急</option>
                        </select>
                      </div>
                      <button type="submit" className="btn btn-primary">
                        <Plus size={14} /> 创建任务
                      </button>
                    </form>
                  </div>

                  {/* 右侧：P08 任务详情、追加式修订、步骤与依赖 */}
                  <div className="card">
                    {!selectedTask ? (
                      <div style={{ color: 'var(--text-secondary)' }}>
                        请在左侧任务树中点击任一任务，查看或修订任务版本（TaskRevision）、步骤（StepRevision）与前置依赖。
                      </div>
                    ) : (
                      <div
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 16,
                        }}
                      >
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            flexWrap: 'wrap',
                            gap: 8,
                          }}
                        >
                          <div>
                            <h3 style={{ fontSize: 18, fontWeight: 700 }}>
                              {selectedTask.title}{' '}
                              <span className="badge badge-blue">
                                当前生效 r{selectedTask.active_revision_no}
                              </span>
                            </h3>
                            <div
                              style={{
                                display: 'flex',
                                gap: 8,
                                marginTop: 6,
                                flexWrap: 'wrap',
                              }}
                            >
                              <StatusBadge
                                status={selectedTask.platform_status}
                                prefix="平台状态"
                              />
                              <StatusBadge
                                status={selectedTask.agent_reported_status}
                                prefix="Agent自报"
                              />
                            </div>
                          </div>

                          <div style={{ display: 'flex', gap: 8 }}>
                            <button
                              type="button"
                              className="btn"
                              onClick={async () => {
                                await apiClient.updateTaskStatus(
                                  currentProject.id,
                                  selectedTask.id,
                                  {
                                    platform_status: 'in_progress',
                                    clear_revalidation: true,
                                  },
                                );
                                await loadProjectContext(currentProject.id);
                                notify('已将任务标记为进行中并解除待重验');
                              }}
                            >
                              标为进行中
                            </button>
                            <button
                              type="button"
                              className="btn btn-primary"
                              onClick={async () => {
                                await apiClient.updateTaskStatus(
                                  currentProject.id,
                                  selectedTask.id,
                                  {
                                    platform_status: 'completed',
                                    clear_revalidation: true,
                                  },
                                );
                                await loadProjectContext(currentProject.id);
                                notify('平台人工验收完成');
                              }}
                            >
                              <CheckCircle size={14} /> 人工验收完成
                            </button>
                          </div>
                        </div>

                        {selectedTask.needs_revalidation && (
                          <div
                            style={{
                              padding: 12,
                              borderRadius: 'var(--radius-sm)',
                              background: 'var(--status-purple-soft)',
                              border: '1px solid rgba(139,92,246,0.45)',
                              color: '#ddd6fe',
                              fontSize: 13,
                            }}
                          >
                            <strong>上游修订待人工重验提示：</strong>
                            {selectedTask.revalidation_reason}
                            （遵循架构铁律：系统不会自动重跑下游任务，请人工复核后点击上方按钮确认）。
                          </div>
                        )}

                        {/* 追加式修订表单 (TaskRevision) */}
                        <form
                          onSubmit={(e) => void handleReviseTask(e)}
                          className="card-elevated"
                          style={{
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 8,
                          }}
                        >
                          <div style={{ fontWeight: 600 }}>
                            追加式修订任务内容（生成 r
                            {selectedTask.active_revision_no + 1}，保留历史快照）
                          </div>
                          <input
                            className="input"
                            value={revTitle}
                            onChange={(e) => setRevTitle(e.target.value)}
                            placeholder="修订后任务标题"
                            required
                          />
                          <textarea
                            className="textarea"
                            rows={2}
                            value={revGoal}
                            onChange={(e) => setRevGoal(e.target.value)}
                            placeholder="修订后任务目标"
                          />
                          <textarea
                            className="textarea"
                            rows={2}
                            value={revCriteria}
                            onChange={(e) => setRevCriteria(e.target.value)}
                            placeholder="修订后验收标准（每行一条）"
                          />
                          <div style={{ display: 'flex', gap: 8 }}>
                            <input
                              className="input"
                              value={revReason}
                              onChange={(e) => setRevReason(e.target.value)}
                              placeholder="本次修订原因说明（如：调整接口边界）"
                            />
                            <button type="submit" className="btn btn-primary">
                              提交新修订版 r{selectedTask.active_revision_no + 1}
                            </button>
                          </div>
                        </form>

                        {/* 任务拆解步骤列表与新建步骤 */}
                        <div>
                          <h4
                            style={{
                              fontSize: 14,
                              fontWeight: 700,
                              marginBottom: 8,
                            }}
                          >
                            拆解步骤 (Steps) 与步骤修订历史
                          </h4>
                          <div
                            style={{
                              display: 'flex',
                              flexDirection: 'column',
                              gap: 8,
                              marginBottom: 10,
                            }}
                          >
                            {projectSteps
                              .filter((s) => s.task_id === selectedTask.id)
                              .map((st) => (
                                <div key={st.id} className="card-elevated">
                                  <div
                                    style={{
                                      display: 'flex',
                                      justifyContent: 'space-between',
                                      alignItems: 'center',
                                    }}
                                  >
                                    <strong>
                                      #{st.seq_order} {st.title} (r
                                      {st.active_revision_no})
                                    </strong>
                                    <div style={{ display: 'flex', gap: 6 }}>
                                      <StatusBadge status={st.platform_status} />
                                      {st.needs_revalidation && (
                                        <span className="badge badge-purple">
                                          待重验
                                        </span>
                                      )}
                                      <button
                                        type="button"
                                        className="btn"
                                        style={{ minHeight: 26, padding: '0 8px' }}
                                        onClick={async () => {
                                          await apiClient.reviseStep(
                                            currentProject.id,
                                            st.id,
                                            {
                                              title: st.title,
                                              instruction: `${st.instruction} [已补充修订说明]`,
                                              change_reason: '步骤细化修订',
                                            },
                                          );
                                          await loadProjectContext(
                                            currentProject.id,
                                          );
                                          await loadTaskContext(
                                            currentProject.id,
                                            selectedTask.id,
                                          );
                                          notify(
                                            `步骤 #${st.seq_order} 已升级至 r${st.active_revision_no + 1}，后续步骤已标为待重验`,
                                          );
                                        }}
                                      >
                                        +修订步骤
                                      </button>
                                    </div>
                                  </div>
                                  {st.instruction && (
                                    <div
                                      style={{
                                        fontSize: 12,
                                        color: 'var(--text-secondary)',
                                        marginTop: 4,
                                      }}
                                    >
                                      {st.instruction}
                                    </div>
                                  )}
                                  {stepRevisionsMap[st.id] &&
                                    stepRevisionsMap[st.id].length > 1 && (
                                      <div
                                        style={{
                                          fontSize: 11,
                                          color: 'var(--text-muted)',
                                          marginTop: 4,
                                        }}
                                      >
                                        历史快照：
                                        {stepRevisionsMap[st.id]
                                          .map(
                                            (r) =>
                                              `r${r.revision_no}(${r.change_reason})`,
                                          )
                                          .join(' → ')}
                                      </div>
                                    )}
                                </div>
                              ))}
                          </div>

                          <form
                            onSubmit={(e) => void handleCreateStep(e)}
                            style={{ display: 'flex', gap: 8 }}
                          >
                            <input
                              className="input"
                              placeholder="新步骤标题"
                              value={newStepTitle}
                              onChange={(e) => setNewStepTitle(e.target.value)}
                              required
                            />
                            <input
                              className="input"
                              placeholder="步骤执行说明"
                              value={newStepInstr}
                              onChange={(e) => setNewStepInstr(e.target.value)}
                            />
                            <button type="submit" className="btn">
                              <Plus size={14} /> 追加步骤
                            </button>
                          </form>
                        </div>

                        {/* 同项目前置依赖 (DAG) 与任务修订历史 */}
                        <div className="grid-2">
                          <div className="card-elevated">
                            <div
                              style={{
                                fontWeight: 600,
                                marginBottom: 8,
                                display: 'flex',
                                alignItems: 'center',
                                gap: 6,
                              }}
                            >
                              <GitBranch size={14} /> 同项目前置依赖 (DAG)
                            </div>
                            <div
                              style={{
                                display: 'flex',
                                flexDirection: 'column',
                                gap: 6,
                                marginBottom: 8,
                              }}
                            >
                              {projectDeps
                                .filter((d) => d.task_id === selectedTask.id)
                                .map((d) => {
                                  const pre = projectTasks.find(
                                    (t) => t.id === d.depends_on_task_id,
                                  );
                                  return (
                                    <div key={d.id} style={{ fontSize: 12 }}>
                                      前置任务：{pre?.title || d.depends_on_task_id}{' '}
                                      ({d.dependency_kind})
                                    </div>
                                  );
                                })}
                            </div>
                            <form
                              onSubmit={(e) => void handleAddDependency(e)}
                              style={{ display: 'flex', gap: 6 }}
                            >
                              <select
                                className="select"
                                value={depPrereqTaskId}
                                onChange={(e) =>
                                  setDepPrereqTaskId(e.target.value)
                                }
                              >
                                <option value="">选择前置依赖任务...</option>
                                {projectTasks
                                  .filter((t) => t.id !== selectedTask.id)
                                  .map((t) => (
                                    <option key={t.id} value={t.id}>
                                      {t.title}
                                    </option>
                                  ))}
                              </select>
                              <button type="submit" className="btn">
                                添加
                              </button>
                            </form>
                          </div>

                          <div className="card-elevated">
                            <div
                              style={{
                                fontWeight: 600,
                                marginBottom: 8,
                                display: 'flex',
                                alignItems: 'center',
                                gap: 6,
                              }}
                            >
                              <Layers size={14} /> 任务修订历史 (TaskRevisions)
                            </div>
                            <div
                              style={{
                                display: 'flex',
                                flexDirection: 'column',
                                gap: 6,
                              }}
                            >
                              {taskRevisions.map((r) => (
                                <div key={r.id} style={{ fontSize: 12 }}>
                                  <span className="badge badge-blue">
                                    r{r.revision_no}
                                  </span>{' '}
                                  <strong>{r.title_snapshot}</strong> —{' '}
                                  {r.change_reason} ({r.created_at})
                                </div>
                              ))}
                              {taskArtifacts.length > 0 && (
                                <div style={{ marginTop: 6, fontSize: 12 }}>
                                  关联产物：{taskArtifacts.length} 份
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )
          ) : activeTab === 'sessions' ? (
            /* ==================== P09-P10 协同会话列表与 v1.3 会话详情页 ==================== */
            <div className="split-layout">
              {/* 左侧：会话列表与登记 */}
              <div className="card">
                <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>
                  P09 项目协同会话 ({overview?.sessions.length || 0})
                </h3>
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 8,
                    marginBottom: 16,
                  }}
                >
                  {(overview?.sessions || []).map((s) => (
                    <div
                      key={s.id}
                      className="card-elevated"
                      style={{
                        cursor: 'pointer',
                        borderColor:
                          s.id === selectedSessionId
                            ? 'var(--accent-blue)'
                            : undefined,
                      }}
                      onClick={() => setSelectedSessionId(s.id)}
                    >
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                        }}
                      >
                        <strong>{s.title}</strong>
                        <StatusBadge status={s.platform_status} />
                      </div>
                      <div
                        style={{
                          fontSize: 12,
                          color: 'var(--text-secondary)',
                          marginTop: 4,
                        }}
                      >
                        项目：{s.project_name || s.project_id} · 通道：
                        {s.source_channel}
                      </div>
                    </div>
                  ))}
                </div>

                {selectedProjectId && (
                  <form
                    onSubmit={(e) => void handleCreateSession(e)}
                    style={{
                      borderTop: '1px solid var(--border-subtle)',
                      paddingTop: 12,
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 8,
                    }}
                  >
                    <div style={{ fontWeight: 600, fontSize: 13 }}>
                      登记当前项目下的协同会话
                    </div>
                    <input
                      className="input"
                      placeholder="会话标题（如：Claude Code 阶段开发）"
                      value={newSessTitle}
                      onChange={(e) => setNewSessTitle(e.target.value)}
                      required
                    />
                    <select
                      className="select"
                      value={newSessTaskId}
                      onChange={(e) => setNewSessTaskId(e.target.value)}
                    >
                      <option value="">关联任务（可选）</option>
                      {projectTasks.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.title}
                        </option>
                      ))}
                    </select>
                    <select
                      className="select"
                      value={newSessRuntimeId}
                      onChange={(e) => setNewSessRuntimeId(e.target.value)}
                    >
                      <option value="">关联 Runtime 实例（可选）</option>
                      {runtimeInstances.map((ri) => (
                        <option key={ri.id} value={ri.id}>
                          {ri.display_name} ({ri.control_level})
                        </option>
                      ))}
                    </select>
                    <button type="submit" className="btn btn-primary">
                      <Plus size={14} /> 登记协同会话
                    </button>
                  </form>
                )}
              </div>

              {/* 右侧：P10 会话详情页（严格遵循 v1.3 收敛规范：不复刻 Agent 聊天界面） */}
              <div className="card">
                {!sessionDetail ? (
                  <div style={{ color: 'var(--text-secondary)' }}>
                    请在左侧选择一个协同会话，查看编排上下文、官方 Remote 入口、关联 Decision/Artifact 与 MCP 进度时间线。
                  </div>
                ) : (
                  <div
                    style={{ display: 'flex', flexDirection: 'column', gap: 16 }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'flex-start',
                        flexWrap: 'wrap',
                        gap: 10,
                      }}
                    >
                      <div>
                        <h3 style={{ fontSize: 18, fontWeight: 700 }}>
                          {sessionDetail.session.title}
                        </h3>
                        <div
                          style={{
                            display: 'flex',
                            gap: 8,
                            marginTop: 6,
                            flexWrap: 'wrap',
                          }}
                        >
                          <StatusBadge
                            status={sessionDetail.session.platform_status}
                            prefix="平台状态"
                          />
                          <StatusBadge
                            status={sessionDetail.session.agent_reported_status}
                            prefix="Agent汇报"
                          />
                          <StatusBadge
                            status={sessionDetail.session.runtime_status}
                            prefix="Runtime"
                          />
                          <StatusBadge
                            status={sessionDetail.session.connection_status}
                            prefix="Remote连接"
                          />
                        </div>
                      </div>

                      {/* 打开官方 Remote 主入口 */}
                      {sessionDetail.external_refs.length > 0 &&
                        sessionDetail.external_refs[0].remote_entry_url && (
                          <a
                            href={sessionDetail.external_refs[0].remote_entry_url}
                            target="_blank"
                            rel="noreferrer"
                            className="btn btn-primary"
                          >
                            <ExternalLink size={15} /> 打开厂商官方 Remote 界面
                          </a>
                        )}
                    </div>

                    <div
                      style={{
                        padding: '10px 12px',
                        borderRadius: 'var(--radius-sm)',
                        background: 'var(--bg-elevated)',
                        border: '1px solid var(--border-subtle)',
                        fontSize: 12,
                        color: 'var(--text-secondary)',
                      }}
                    >
                      <strong>v1.3 会话边界与能力分级声明：</strong>
                      Agent 原生多轮聊天、工具调用、权限审批与终端过程由对应 Runtime 官方界面承载；本页聚焦任务编排上下文、官方 Remote 入口、独立连接状态、Decision 拍板与 MCP 时间线。
                      当前绑定 Runtime 控制等级为{' '}
                      <code>
                        {sessionDetail.runtime_instance
                          ? CONTROL_LEVEL_LABELS[
                              sessionDetail.runtime_instance.control_level
                            ] || sessionDetail.runtime_instance.control_level
                          : '未绑定 (L0)'}
                      </code>
                      （Phase 3 严格只读展示能力证据与官方 Remote 跳转入口，不提供受控指令发送框或模拟控制功能）。
                    </div>

                    {/* 独立 Connection Status 状态管理栏 */}
                    <div
                      className="card-elevated"
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        flexWrap: 'wrap',
                        gap: 10,
                      }}
                    >
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 13 }}>
                          Connection Status 独立远程连接状态管理（断线不判败）
                        </div>
                        <div
                          style={{
                            fontSize: 12,
                            color: 'var(--text-secondary)',
                            marginTop: 2,
                          }}
                        >
                          远程连接状态与平台会话/任务执行状态严格分离：将连接切为{' '}
                          <code>disconnected</code> 仅记录通道离线，绝不把任务或会话误判为执行失败。
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {(
                          ['connected', 'disconnected', 'unknown'] as const
                        ).map((stVal) => (
                          <button
                            key={stVal}
                            type="button"
                            className={`btn ${
                              sessionDetail.session.connection_status === stVal
                                ? 'btn-primary'
                                : ''
                            }`}
                            onClick={() =>
                              void handleUpdateConnectionStatus(stVal)
                            }
                          >
                            设为 {stVal}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* 编排上下文与官方 Remote 绑定 */}
                    <div className="grid-2">
                      <div className="card-elevated">
                        <div style={{ fontWeight: 600, marginBottom: 6 }}>
                          全链路编排身份映射 (Project → Task → Runtime → Machine)
                        </div>
                        <div style={{ fontSize: 13 }}>
                          所属项目：{sessionDetail.project.name} (
                          <code>{sessionDetail.project.slug}</code>)
                        </div>
                        <div style={{ fontSize: 13, marginTop: 4 }}>
                          关联任务：
                          {sessionDetail.task
                            ? `${sessionDetail.task.title} (r${sessionDetail.task.active_revision_no} · ${sessionDetail.task.platform_status})`
                            : '未绑定具体任务'}
                        </div>
                        <div style={{ fontSize: 13, marginTop: 4 }}>
                          Runtime 实例：
                          {sessionDetail.runtime_instance
                            ? `${sessionDetail.runtime_instance.display_name} (${
                                CONTROL_LEVEL_LABELS[
                                  sessionDetail.runtime_instance.control_level
                                ] || sessionDetail.runtime_instance.control_level
                              })`
                            : '未绑定'}
                        </div>
                        <div style={{ fontSize: 13, marginTop: 4 }}>
                          宿主机器 (Machine)：
                          {sessionDetail.runtime_instance?.machine
                            ? `${sessionDetail.runtime_instance.machine.display_name} (${sessionDetail.runtime_instance.machine.os} · ${sessionDetail.runtime_instance.machine.connection_kind} · ${sessionDetail.runtime_instance.machine.health_status})`
                            : '默认本机'}
                        </div>

                        {/* 已登记 ExternalSessionRef 列表 */}
                        <div
                          style={{
                            marginTop: 10,
                            paddingTop: 10,
                            borderTop: '1px solid var(--border-subtle)',
                          }}
                        >
                          <div
                            style={{
                              fontWeight: 600,
                              fontSize: 12,
                              marginBottom: 6,
                            }}
                          >
                            已绑定外部原生 Session 与官方 Remote 入口 (
                            {sessionDetail.external_refs.length})
                          </div>
                          {sessionDetail.external_refs.length === 0 ? (
                            <div
                              style={{
                                fontSize: 12,
                                color: 'var(--text-muted)',
                              }}
                            >
                              尚未登记外部原生会话映射，请在右侧表单登记。
                            </div>
                          ) : (
                            <div
                              style={{
                                display: 'flex',
                                flexDirection: 'column',
                                gap: 8,
                              }}
                            >
                              {sessionDetail.external_refs.map((ref) => (
                                <div
                                  key={ref.id}
                                  style={{
                                    padding: 8,
                                    borderRadius: 'var(--radius-sm)',
                                    background: 'var(--bg-surface)',
                                    border: '1px solid var(--border-subtle)',
                                    fontSize: 12,
                                  }}
                                >
                                  <div
                                    style={{
                                      display: 'flex',
                                      justifyContent: 'space-between',
                                      alignItems: 'center',
                                      gap: 6,
                                      flexWrap: 'wrap',
                                    }}
                                  >
                                    <span>
                                      原生句柄：
                                      <code>
                                        {ref.external_session_id || '未填写'}
                                      </code>{' '}
                                      · 入口类型：
                                      <code>{ref.remote_entry_kind}</code>
                                    </span>
                                    <StatusBadge
                                      status={ref.link_status}
                                      prefix="链接"
                                    />
                                  </div>
                                  {ref.remote_entry_url && (
                                    <div
                                      style={{
                                        marginTop: 4,
                                        display: 'flex',
                                        justifyContent: 'space-between',
                                        alignItems: 'center',
                                        gap: 8,
                                        flexWrap: 'wrap',
                                      }}
                                    >
                                      <code
                                        style={{
                                          color: 'var(--text-secondary)',
                                          wordBreak: 'break-all',
                                        }}
                                      >
                                        脱敏展示：
                                        {ref.masked_remote_url ||
                                          ref.remote_entry_url}
                                      </code>
                                      <a
                                        href={ref.remote_entry_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="btn"
                                        style={{
                                          minHeight: 28,
                                          padding: '0 8px',
                                        }}
                                      >
                                        <ExternalLink size={12} /> 跳转官方入口
                                      </a>
                                    </div>
                                  )}
                                  <div
                                    style={{
                                      marginTop: 6,
                                      display: 'flex',
                                      gap: 6,
                                    }}
                                  >
                                    {(
                                      [
                                        'active',
                                        'stale',
                                        'disconnected',
                                      ] as const
                                    ).map((ls) => (
                                      <button
                                        key={ls}
                                        type="button"
                                        className="btn"
                                        style={{
                                          minHeight: 26,
                                          padding: '0 8px',
                                          fontSize: 11,
                                        }}
                                        onClick={() =>
                                          void handleUpdateExternalRefStatus(
                                            ref.id,
                                            ls,
                                          )
                                        }
                                      >
                                        标记 {ls}
                                      </button>
                                    ))}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>

                      <form
                        onSubmit={(e) => void handleBindExternalRef(e)}
                        className="card-elevated"
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 6,
                        }}
                      >
                        <div style={{ fontWeight: 600 }}>
                          登记外部原生 Session 与脱敏官方 Remote 入口
                        </div>
                        <select
                          className="select"
                          value={extRefRuntimeId}
                          onChange={(e) => setExtRefRuntimeId(e.target.value)}
                          required
                        >
                          <option value="">选择 Runtime 实例...</option>
                          {runtimeInstances.map((ri) => (
                            <option key={ri.id} value={ri.id}>
                              {ri.display_name} ({ri.control_level})
                            </option>
                          ))}
                        </select>
                        <input
                          className="input"
                          placeholder="外部原生会话标识（如 claude-sess-01，留空自动生成）"
                          value={extRefNativeId}
                          onChange={(e) => setExtRefNativeId(e.target.value)}
                        />
                        <select
                          className="select"
                          value={extRefKind}
                          onChange={(e) => setExtRefKind(e.target.value)}
                        >
                          <option value="claude_remote_control">
                            Claude Remote Control
                          </option>
                          <option value="chatgpt_remote">
                            ChatGPT / Codex Remote
                          </option>
                          <option value="opencode_web">OpenCode Web</option>
                          <option value="antigravity_remote">
                            Antigravity Official Entry
                          </option>
                        </select>
                        <input
                          className="input"
                          placeholder="https://claude.ai/code (仅限 http/https，严禁含 token 参数)"
                          value={extRefUrl}
                          onChange={(e) => setExtRefUrl(e.target.value)}
                        />
                        <button type="submit" className="btn btn-primary">
                          保存外部会话与官方 Remote 映射
                        </button>
                      </form>
                    </div>

                    {/* Runtime 能力与验证证据只读面板 */}
                    {sessionDetail.runtime_instance && (
                      <div className="card-elevated">
                        <div style={{ fontWeight: 600, marginBottom: 8 }}>
                          关联 Runtime 能力清单与验证证据（未经验证项严格标为 unverified）
                        </div>
                        <div
                          style={{
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 6,
                          }}
                        >
                          {sessionDetail.runtime_instance.capabilities.map(
                            (cap) => (
                              <div
                                key={cap.capability}
                                style={{
                                  fontSize: 12,
                                  display: 'flex',
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                  gap: 8,
                                  flexWrap: 'wrap',
                                  padding: '4px 0',
                                  borderBottom:
                                    '1px solid var(--border-subtle)',
                                }}
                              >
                                <span>
                                  <code>{cap.capability}</code> · 证据来源：
                                  <code>{cap.evidence}</code> · 版本：
                                  <code>{cap.runtime_version}</code>
                                  {cap.verified_protocol_versions &&
                                  cap.verified_protocol_versions.length > 0
                                    ? ` · 已验证协议：[${cap.verified_protocol_versions.join(', ')}]`
                                    : ''}
                                  {cap.verified_at
                                    ? ` · 验证时间：${cap.verified_at}`
                                    : ''}{' '}
                                  — {cap.limitations}
                                </span>
                                <StatusBadge status={cap.support_status} />
                              </div>
                            ),
                          )}
                        </div>
                      </div>
                    )}

                    {/* 关联 Decision 与 MCP 回写进度时间线 */}
                    <div className="grid-2">
                      <div className="card-elevated">
                        <div style={{ fontWeight: 600, marginBottom: 8 }}>
                          关联交互式决策 ({sessionDetail.decisions.length})
                        </div>
                        {sessionDetail.decisions.map((d) => (
                          <div
                            key={d.id}
                            style={{
                              fontSize: 12,
                              padding: '6px 0',
                              borderBottom: '1px solid var(--border-subtle)',
                            }}
                          >
                            <StatusBadge status={d.status} />{' '}
                            <strong>{d.title}</strong>
                            {d.resolved_by && (
                              <span style={{ color: 'var(--text-muted)' }}>
                                {' '}
                                · 处理方式: {d.resolved_by}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>

                      <div className="card-elevated">
                        <div style={{ fontWeight: 600, marginBottom: 8 }}>
                          MCP 回写与会话事件时间线
                        </div>
                        <div
                          style={{
                            maxHeight: 220,
                            overflowY: 'auto',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 6,
                          }}
                        >
                          {sessionDetail.timeline_events.map((e) => (
                            <div key={e.seq_id} style={{ fontSize: 12 }}>
                              <span className="badge badge-blue">
                                #{e.seq_id} {e.event_type}
                              </span>{' '}
                              <span style={{ color: 'var(--text-muted)' }}>
                                {e.created_at}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : activeTab === 'decisions' ? (
            /* ==================== P02-P03 待决策与历史裁决中心 ==================== */
            <div className="card">
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: 14,
                }}
              >
                <h3 style={{ fontSize: 16, fontWeight: 700 }}>
                  P02-P03 交互式决策与风险门禁中心 ({allDecisions.length})
                </h3>
                {selectedProjectId && (
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => void handleTriggerDemoDecision()}
                  >
                    + 模拟 Agent 发起交互式提问
                  </button>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {allDecisions.map((d) => (
                  <div key={d.id} className="card-elevated">
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        flexWrap: 'wrap',
                        gap: 8,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <StatusBadge status={d.status} />
                        <strong>{d.title}</strong>
                        <span className="badge badge-gray">
                          风险: {d.risk_level}
                        </span>
                        <span className="badge badge-gray">
                          超时策略: {d.timeout_policy}
                        </span>
                      </div>
                      <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                        {d.created_at}
                      </span>
                    </div>
                    {d.context_summary && (
                      <div
                        style={{
                          fontSize: 12,
                          color: 'var(--text-secondary)',
                          marginTop: 6,
                        }}
                      >
                        {d.context_summary}
                      </div>
                    )}
                    {d.status === 'resolved' && (
                      <div
                        style={{
                          fontSize: 12,
                          color: '#6ee7b7',
                          marginTop: 6,
                        }}
                      >
                        已选方案：{d.chosen_option_id || '自定义说明'} · 来源：
                        {d.resolved_by} · 备注：{d.custom_note || '无'}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            /* ==================== P14 Runtime & Remote 注册中心 ==================== */
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* Remote Registry 统一台账（按当前选中项目严格权限隔离） */}
              <div className="card">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: 12,
                    flexWrap: 'wrap',
                    gap: 8,
                  }}
                >
                  <div>
                    <h3 style={{ fontSize: 16, fontWeight: 700 }}>
                      Remote Registry 官方远程会话登记台账（项目级权限隔离：
                      {currentProject ? currentProject.name : '未选择项目'}）
                    </h3>
                    <div
                      style={{
                        fontSize: 12,
                        color: 'var(--text-secondary)',
                        marginTop: 2,
                      }}
                    >
                      全链路映射：Project → Task → Session → RuntimeInstance →
                      Machine → Native Session → 脱敏官方 Remote 入口
                    </div>
                  </div>
                  <span className="badge badge-blue">
                    当前项目已登记 Remote: {projectRemotes.length}
                  </span>
                </div>

                {!selectedProjectId ? (
                  <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                    请先在顶部下拉框选择目标项目以查看该项目隔离的 Remote Registry 台账。
                  </div>
                ) : projectRemotes.length === 0 ? (
                  <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                    当前项目尚无登记的外部会话与官方 Remote 入口，可在「P09-P10 协同会话」中绑定。
                  </div>
                ) : (
                  <div
                    style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
                  >
                    {projectRemotes.map((entry) => (
                      <div key={entry.id} className="card-elevated">
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            flexWrap: 'wrap',
                            gap: 8,
                          }}
                        >
                          <div>
                            <strong>{entry.session.title}</strong>{' '}
                            <span
                              style={{
                                fontSize: 12,
                                color: 'var(--text-secondary)',
                              }}
                            >
                              · 项目：{entry.project.name}
                              {entry.task
                                ? ` · 任务：${entry.task.title}`
                                : ''}
                            </span>
                          </div>
                          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                            <StatusBadge
                              status={entry.session.platform_status}
                              prefix="会话"
                            />
                            <StatusBadge
                              status={entry.session.connection_status}
                              prefix="Remote连接"
                            />
                            <StatusBadge
                              status={entry.external_ref.link_status}
                              prefix="入口"
                            />
                          </div>
                        </div>

                        <div
                          style={{
                            fontSize: 12,
                            color: 'var(--text-secondary)',
                            marginTop: 6,
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            flexWrap: 'wrap',
                            gap: 8,
                          }}
                        >
                          <span>
                            Runtime：
                            <code>
                              {entry.runtime_instance?.display_name || '未知'} (
                              {entry.runtime_instance?.control_level || 'L0'})
                            </code>{' '}
                            · 机器：
                            <code>
                              {entry.machine?.display_name || 'local-machine'}
                            </code>{' '}
                            · 原生句柄：
                            <code>
                              {entry.external_ref.external_session_id || '未填'}
                            </code>{' '}
                            · 官方入口：
                            <code>
                              {entry.external_ref.masked_remote_url ||
                                entry.external_ref.remote_entry_url ||
                                '未配置 URL'}
                            </code>
                          </span>
                          <div style={{ display: 'flex', gap: 6 }}>
                            <button
                              type="button"
                              className="btn"
                              style={{ minHeight: 28, padding: '0 8px' }}
                              onClick={() => {
                                setSelectedSessionId(entry.session.id);
                                setActiveTab('sessions');
                              }}
                            >
                              进入 P10 详情
                            </button>
                            {entry.external_ref.remote_entry_url && (
                              <a
                                href={entry.external_ref.remote_entry_url}
                                target="_blank"
                                rel="noreferrer"
                                className="btn btn-primary"
                                style={{ minHeight: 28, padding: '0 8px' }}
                              >
                                <ExternalLink size={12} /> 打开官方 Remote
                              </a>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="card">
                <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>
                  4 大官方 Runtime 诚实能力声明矩阵（未知项严格标为 unverified）
                </h3>
                <div
                  style={{
                    fontSize: 12,
                    color: 'var(--text-secondary)',
                    marginBottom: 12,
                  }}
                >
                  统一能力分级：<code>L0 官方 Remote 入口</code> ·{' '}
                  <code>L1 Runtime 注册、状态与能力信息管理</code> ·{' '}
                  <code>L2 程序化控制（SDK/HTTP/Stdio 是实现途径）</code> ·{' '}
                  <code>L3 审批与恢复能力</code>
                </div>
                <div className="grid-2">
                  {providers.map((p) => (
                    <div key={p.id} className="card-elevated">
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          marginBottom: 8,
                        }}
                      >
                        <strong>
                          {p.display_name} ({p.provider_key})
                        </strong>
                        <span className="badge badge-blue">
                          {p.integration_type}
                        </span>
                      </div>
                      <div
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 6,
                        }}
                      >
                        {p.default_capabilities.map((cap) => (
                          <div
                            key={cap.capability}
                            style={{
                              fontSize: 12,
                              display: 'flex',
                              justifyContent: 'space-between',
                              gap: 8,
                            }}
                          >
                            <span>
                              <code>{cap.capability}</code>
                              {cap.verified_protocol_versions &&
                              cap.verified_protocol_versions.length > 0
                                ? ` [${cap.verified_protocol_versions.join(', ')}]`
                                : ''}{' '}
                              — {cap.limitations}
                            </span>
                            <StatusBadge status={cap.support_status} />
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="grid-2">
                <div className="card">
                  <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>
                    已登记 Runtime 实例 ({runtimeInstances.length}) 与宿主机器 (
                    {machines.length})
                  </h3>
                  <div
                    style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
                  >
                    {runtimeInstances.map((inst) => (
                      <div key={inst.id} className="card-elevated">
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                          }}
                        >
                          <strong>{inst.display_name}</strong>
                          <div style={{ display: 'flex', gap: 6 }}>
                            <span className="badge badge-blue">
                              {CONTROL_LEVEL_LABELS[inst.control_level] ||
                                inst.control_level}
                            </span>
                            <StatusBadge status={inst.health_status} />
                          </div>
                        </div>
                        <div
                          style={{
                            fontSize: 12,
                            color: 'var(--text-muted)',
                            marginTop: 4,
                          }}
                        >
                          版本：{inst.runtime_version} · 机器：
                          {inst.machine?.display_name || inst.machine_id}
                        </div>
                        {inst.model_endpoint_descriptor && (
                          <div
                            style={{
                              fontSize: 12,
                              color: 'var(--text-secondary)',
                              marginTop: 4,
                            }}
                          >
                            兼容模型环境标签（非敏感）：
                            {inst.model_endpoint_descriptor.provider_label} /{' '}
                            {inst.model_endpoint_descriptor.model_label} (
                            {inst.model_endpoint_descriptor.verified_status})
                          </div>
                        )}
                      </div>
                    ))}
                  </div>

                  {/* 宿主机器列表与状态切换 */}
                  <div
                    style={{
                      marginTop: 14,
                      paddingTop: 12,
                      borderTop: '1px solid var(--border-subtle)',
                    }}
                  >
                    <div
                      style={{
                        fontWeight: 600,
                        fontSize: 13,
                        marginBottom: 8,
                      }}
                    >
                      宿主机器列表 (Machines)
                    </div>
                    <div
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 6,
                      }}
                    >
                      {machines.map((m) => (
                        <div
                          key={m.id}
                          className="card-elevated"
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            flexWrap: 'wrap',
                            gap: 8,
                            fontSize: 12,
                          }}
                        >
                          <span>
                            <strong>{m.display_name}</strong> ({m.os} ·{' '}
                            {m.connection_kind} · {m.hostname_alias || m.id})
                          </span>
                          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                            <StatusBadge status={m.health_status} />
                            {(
                              ['online', 'degraded', 'offline'] as const
                            ).map((hst) => (
                              <button
                                key={hst}
                                type="button"
                                className="btn"
                                style={{
                                  minHeight: 24,
                                  padding: '0 6px',
                                  fontSize: 11,
                                }}
                                onClick={() =>
                                  void handleToggleMachineHealth(m.id, hst)
                                }
                              >
                                {hst}
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  <form
                    onSubmit={(e) => void handleRegisterRuntimeInstance(e)}
                    className="card"
                    style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
                  >
                    <h3 style={{ fontSize: 15, fontWeight: 700 }}>
                      登记 Runtime 实例（L2/L3 强制要求实测验证证据）
                    </h3>
                    <select
                      className="select"
                      value={newInstProvider}
                      onChange={(e) => setNewInstProvider(e.target.value)}
                    >
                      <option value="claude_code">Claude Code</option>
                      <option value="codex">OpenAI Codex</option>
                      <option value="opencode">OpenCode</option>
                      <option value="antigravity">Google Antigravity</option>
                    </select>
                    <select
                      className="select"
                      value={newInstMachineId}
                      onChange={(e) => setNewInstMachineId(e.target.value)}
                    >
                      {machines.map((m) => (
                        <option key={m.id} value={m.id}>
                          宿主机器：{m.display_name} ({m.os})
                        </option>
                      ))}
                    </select>
                    <select
                      className="select"
                      value={newInstLevel}
                      onChange={(e) =>
                        setNewInstLevel(e.target.value as 'L0' | 'L1')
                      }
                    >
                      <option value="L0">
                        L0 · 官方 Remote 入口（默认安全基线）
                      </option>
                      <option value="L1">
                        L1 · Runtime 注册、状态与能力信息管理
                      </option>
                    </select>
                    <input
                      className="input"
                      placeholder="Runtime 版本号（如 1.0.24）"
                      value={newInstVersion}
                      onChange={(e) => setNewInstVersion(e.target.value)}
                    />
                    <input
                      className="input"
                      placeholder="实例显示名称（留空自动生成）"
                      value={newInstName}
                      onChange={(e) => setNewInstName(e.target.value)}
                    />
                    <input
                      className="input"
                      placeholder="可选模型服务标签（如 GLM Coding Plan，严禁填 URL 或 API Key）"
                      value={newInstProviderLabel}
                      onChange={(e) => setNewInstProviderLabel(e.target.value)}
                    />
                    <input
                      className="input"
                      placeholder="可选模型显示名（如 GLM-4.6 / DeepSeek-V3.1）"
                      value={newInstModelLabel}
                      onChange={(e) => setNewInstModelLabel(e.target.value)}
                    />
                    <button type="submit" className="btn btn-primary">
                      登记 Runtime 实例
                    </button>
                  </form>

                  <form
                    onSubmit={(e) => void handleRegisterMachine(e)}
                    className="card"
                    style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
                  >
                    <h3 style={{ fontSize: 15, fontWeight: 700 }}>
                      登记新宿主机器 (Machine)
                    </h3>
                    <input
                      className="input"
                      placeholder="机器显示名称（如：MacBook Pro 远端开发机）"
                      value={newMachineName}
                      onChange={(e) => setNewMachineName(e.target.value)}
                      required
                    />
                    <div className="grid-2">
                      <select
                        className="select"
                        value={newMachineOs}
                        onChange={(e) => setNewMachineOs(e.target.value)}
                      >
                        <option value="windows">Windows</option>
                        <option value="macos">macOS</option>
                        <option value="linux">Linux</option>
                      </select>
                      <select
                        className="select"
                        value={newMachineKind}
                        onChange={(e) => setNewMachineKind(e.target.value)}
                      >
                        <option value="local">Local</option>
                        <option value="tailscale">Tailscale</option>
                        <option value="lan">LAN</option>
                        <option value="vendor_remote">Vendor Remote</option>
                      </select>
                    </div>
                    <input
                      className="input"
                      placeholder="主机名或网络别名（如 dev-mac.tailnet）"
                      value={newMachineAlias}
                      onChange={(e) => setNewMachineAlias(e.target.value)}
                    />
                    <button type="submit" className="btn">
                      登记宿主机器
                    </button>
                  </form>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>

      {/* 移动端底部触控导航栏 (>=48px) */}
      <nav className="mobile-bottom-nav">
        <button
          type="button"
          className={`mobile-nav-btn ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          <Activity size={18} />
          <span>总览</span>
        </button>
        <button
          type="button"
          className={`mobile-nav-btn ${activeTab === 'projects' ? 'active' : ''}`}
          onClick={() => setActiveTab('projects')}
        >
          <ListTodo size={18} />
          <span>项目与任务</span>
        </button>
        <button
          type="button"
          className={`mobile-nav-btn ${activeTab === 'sessions' ? 'active' : ''}`}
          onClick={() => setActiveTab('sessions')}
        >
          <TerminalSquare size={18} />
          <span>协同会话</span>
        </button>
        <button
          type="button"
          className={`mobile-nav-btn ${activeTab === 'decisions' ? 'active' : ''}`}
          onClick={() => {
            setActiveTab('decisions');
            void loadRuntimesAndDecisions();
          }}
        >
          <AlertCircle size={18} />
          <span>决策({overview?.counts.pending_decisions || 0})</span>
        </button>
        <button
          type="button"
          className={`mobile-nav-btn ${activeTab === 'runtimes' ? 'active' : ''}`}
          onClick={() => {
            setActiveTab('runtimes');
            void loadRuntimesAndDecisions();
          }}
        >
          <Cpu size={18} />
          <span>Runtime</span>
        </button>
      </nav>

      {/* P05 新建项目弹窗 */}
      {showNewProjectModal && (
        <div className="modal-backdrop">
          <form
            onSubmit={(e) => void handleCreateProject(e)}
            className="modal-panel"
            style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
          >
            <h3 style={{ fontSize: 18, fontWeight: 700 }}>
              P05 新建研发项目 (Project)
            </h3>
            <input
              className="input"
              placeholder="项目显示名称（如：AgentDeck 核心重构）"
              value={newProjName}
              onChange={(e) => setNewProjName(e.target.value)}
              required
            />
            <input
              className="input"
              placeholder="本地代码仓库绝对路径（如：d:/Development Files/PythonProjects/stu/AgentDeck）"
              value={newProjRoot}
              onChange={(e) => setNewProjRoot(e.target.value)}
              required
            />
            <textarea
              className="textarea"
              rows={2}
              placeholder="项目背景与目标说明"
              value={newProjDesc}
              onChange={(e) => setNewProjDesc(e.target.value)}
            />
            <input
              className="input"
              placeholder="当前下一步关键动作摘要"
              value={newProjNextAction}
              onChange={(e) => setNewProjNextAction(e.target.value)}
            />
            <div
              style={{
                display: 'flex',
                justifyContent: 'flex-end',
                gap: 8,
                marginTop: 6,
              }}
            >
              <button
                type="button"
                className="btn"
                onClick={() => setShowNewProjectModal(false)}
              >
                取消
              </button>
              <button type="submit" className="btn btn-primary">
                确认创建项目
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 全局跨页面交互式决策弹窗 */}
      <InteractiveDecisionModal
        decision={activePendingDecision}
        pendingCount={overview?.counts.pending_decisions || 0}
        onResolved={() => {
          void refreshOverview();
          void loadRuntimesAndDecisions();
          if (selectedProjectId) void loadProjectContext(selectedProjectId);
          if (selectedSessionId) void loadSessionContext(selectedSessionId);
          notify('交互式决策已完成幂等同步');
        }}
      />
    </div>
  );
};

