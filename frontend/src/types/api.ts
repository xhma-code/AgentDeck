export interface ProjectItem {
  id: string;
  slug: string;
  name: string;
  description: string;
  root_path: string;
  git_remote: string | null;
  status: 'active' | 'paused' | 'archived';
  summary_next_action: string;
  version: number;
  created_at: string;
  updated_at: string;
  stats?: {
    total_tasks: number;
    completed_tasks: number;
    in_progress_tasks: number;
    needs_revalidation_tasks: number;
    pending_decisions: number;
    session_count: number;
  };
}

export interface McpTokenItem {
  id: string;
  project_id: string;
  token_name: string;
  token_prefix: string;
  scopes: string[];
  is_revoked: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface TaskItem {
  id: string;
  project_id: string;
  parent_task_id: string | null;
  title: string;
  goal: string;
  acceptance_criteria: string[];
  platform_status: string;
  agent_reported_status: string;
  priority: 'low' | 'medium' | 'high' | 'critical';
  sort_order: number;
  active_revision_no: number;
  needs_revalidation: boolean;
  revalidation_reason: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TaskRevisionItem {
  id: string;
  project_id: string;
  task_id: string;
  revision_no: number;
  title_snapshot: string;
  goal_snapshot: string;
  acceptance_criteria: string[];
  change_reason: string;
  changed_by: string;
  created_at: string;
}

export interface StepItem {
  id: string;
  project_id: string;
  task_id: string;
  seq_order: number;
  title: string;
  instruction: string;
  platform_status: string;
  agent_reported_status: string;
  active_revision_no: number;
  needs_revalidation: boolean;
  revalidation_reason: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface StepRevisionItem {
  id: string;
  project_id: string;
  task_id: string;
  step_id: string;
  revision_no: number;
  title_snapshot: string;
  instruction_snapshot: string;
  change_reason: string;
  changed_by: string;
  created_at: string;
}

export interface TaskDependencyItem {
  id: string;
  project_id: string;
  task_id: string;
  depends_on_task_id: string;
  dependency_kind: string;
  created_at: string;
}

export interface DecisionOptionItem {
  option_id: string;
  label: string;
  description: string;
  is_recommended: boolean;
}

export interface DecisionItem {
  id: string;
  project_id: string;
  project_name?: string;
  task_id: string | null;
  step_id: string | null;
  session_id: string | null;
  execution_id: string | null;
  decision_type: string;
  risk_level: 'low' | 'normal' | 'high' | 'critical';
  title: string;
  context_summary: string;
  options: DecisionOptionItem[];
  recommended_option_id: string | null;
  allow_custom_input: boolean;
  timeout_policy: 'auto_submit_recommended' | 'manual_only' | 'expire_on_timeout';
  timeout_sec: number;
  status: 'pending' | 'resolved' | 'expired' | 'cancelled';
  chosen_option_id: string | null;
  custom_note: string;
  resolved_by: string | null;
  version: number;
  created_at: string;
  resolved_at: string | null;
  updated_at: string;
}

export interface ExternalSessionRefItem {
  id: string;
  project_id: string;
  session_id: string;
  runtime_instance_id: string;
  external_session_id: string;
  remote_entry_kind: string;
  remote_entry_url: string;
  masked_remote_url?: string;
  link_status: string;
  last_activity_at: string | null;
  created_at: string;
  updated_at?: string;
}

export interface RemoteRegistryEntryItem {
  id: string;
  project: {
    id: string;
    name: string;
    slug: string;
  };
  task: {
    id: string;
    title: string;
    platform_status: string;
  } | null;
  session: {
    id: string;
    title: string;
    platform_status: string;
    agent_reported_status: string;
    runtime_status: string;
    connection_status: string;
    status_seq: number;
  };
  runtime_instance: {
    id: string;
    display_name: string;
    runtime_version: string;
    connector_version: string;
    control_level: 'L0' | 'L1' | 'L2' | 'L3';
    health_status: string;
    provider_key: string;
    provider_name: string;
  } | null;
  machine: {
    id: string;
    display_name: string;
    os: string;
    hostname_alias: string;
    connection_kind: string;
    health_status: string;
  } | null;
  external_ref: ExternalSessionRefItem;
}

export interface SessionItem {
  id: string;
  project_id: string;
  project_name?: string;
  task_id: string | null;
  runtime_instance_id: string | null;
  title: string;
  source_channel: string;
  platform_status: string;
  agent_reported_status: string;
  runtime_status: string;
  connection_status: string;
  status_seq: number;
  last_activity_at: string | null;
  created_at: string;
  updated_at: string;
  external_refs?: ExternalSessionRefItem[];
}

export interface CapabilityManifestItem {
  capability: string;
  support_status: 'supported' | 'unsupported' | 'unverified';
  evidence: string;
  runtime_version: string;
  connector_version: string;
  verified_at: string | null;
  verified_protocol_versions?: string[];
  limitations: string;
}

export interface RuntimeProviderItem {
  id: string;
  provider_key: 'claude_code' | 'codex' | 'opencode' | 'antigravity';
  display_name: string;
  integration_type: string;
  documentation_url: string;
  default_capabilities: CapabilityManifestItem[];
  enabled: boolean;
}

export interface MachineItem {
  id: string;
  display_name: string;
  os: string;
  hostname_alias: string;
  connection_kind: string;
  health_status: string;
  last_seen_at: string | null;
}

export interface RuntimeInstanceItem {
  id: string;
  provider_id: string;
  machine_id: string;
  display_name: string;
  runtime_version: string;
  connector_version: string;
  control_level: 'L0' | 'L1' | 'L2' | 'L3';
  health_status: string;
  capabilities: CapabilityManifestItem[];
  model_endpoint_descriptor: {
    provider_label: string;
    model_label: string;
    endpoint_kind: string;
    configuration_owner: string;
    verified_status: string;
  } | null;
  provider?: RuntimeProviderItem;
  machine?: MachineItem;
}

export interface ArtifactItem {
  id: string;
  project_id: string;
  task_id: string | null;
  session_id: string | null;
  execution_id: string | null;
  artifact_type: string;
  title: string;
  relative_path: string;
  summary_content: string;
  checksum_sha256: string;
  source_task_revision_no: number | null;
  created_by: string;
  created_at: string;
}

export interface EventItem {
  seq_id: number;
  event_id: string;
  project_id: string | null;
  entity_type: string;
  entity_id: string;
  event_type: string;
  actor_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface OverviewResponse {
  ok: boolean;
  counts: {
    total_projects: number;
    active_projects: number;
    pending_decisions: number;
    active_sessions: number;
    runtime_instances: number;
  };
  projects: ProjectItem[];
  pending_decisions: DecisionItem[];
  sessions: SessionItem[];
  recent_events: EventItem[];
  runtime_providers: RuntimeProviderItem[];
}

