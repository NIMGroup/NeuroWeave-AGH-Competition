import type { Dataset, SamplePathCoverage, SamplePathMatch } from '../datasets/datasetsApi';

export type WorkflowStatus =
  | 'awaiting_method_choice'
  | 'awaiting_input_binding'
  | 'chain_ready'
  | 'awaiting_parameter_review'
  | 'awaiting_dry_run'
  | 'ready'
  | 'confirmed';

export type ParameterValue = string | number | boolean | null;

export type ParameterOption = {
  value: ParameterValue;
  label: string;
  description?: string;
};

export type ParameterSpec = {
  id: string;
  argument: string;
  label: string;
  description: string;
  type: 'string' | 'integer' | 'number' | 'boolean';
  control?: 'auto' | 'select' | 'number' | 'text' | 'switch';
  level?: 'basic' | 'advanced';
  group?: string;
  unit: string;
  required: boolean;
  default: ParameterValue;
  options: ParameterOption[];
  minimum?: number | null;
  maximum?: number | null;
  step?: number | null;
  placeholder?: string;
  visible_when?: Record<string, ParameterValue[]>;
  required_when?: Record<string, ParameterValue[]>;
  enabled_when?: Record<string, ParameterValue[]>;
  conflicts_with?: string[];
  condition_help?: string;
  usage?: string;
  software_default?: string;
  documentation_url?: string;
};

export type InputSlotSpec = {
  id: string;
  type: string;
  label: string;
  description: string;
  required: boolean;
  required_when: Record<string, ParameterValue[]>;
  required_when_any?: Record<string, ParameterValue[]>[];
  cardinality: 'one' | 'many';
  binding: 'file' | 'folder' | 'file_bundle' | 'artifact';
  formats: string[];
  companions: string[];
  source_options: Array<'dataset' | 'existing_path' | 'upstream_output'>;
  validation: string[];
  discovery_patterns?: string[];
  condition_help?: string;
};

export type OutputSlotSpec = {
  id: string;
  type: string;
  label: string;
  description: string;
  format: string;
  primary: boolean;
  path_template: string;
  generated_when: Record<string, ParameterValue[]>;
};

export type InputBinding = {
  confirmed_by_user?: boolean;
  per_sample_requested?: boolean;
  slot_id: string;
  artifact_type: string;
  source: 'dataset' | 'existing_path' | 'upstream_output';
  label: string;
  root_id: string | null;
  relative_path: string | null;
  absolute_path: string | null;
  upstream_step_id: string | null;
  output_slot_id: string | null;
  binding_mode: 'single' | 'per_sample' | 'dataset';
  dataset_id: string | null;
  path_template: string;
  variables: string[];
  coverage: SamplePathCoverage | null;
  sample_matches: SamplePathMatch[];
  history?: {
    run_id: string;
    group_id: string;
    dataset_id: string;
    allow_partial?: boolean;
  } | null;
  status: 'validated' | 'selected' | 'planned';
  messages: string[];
  deferred_resolution?: boolean;
  declaration_fact_id?: string;
};

export type SampleScope = {
  total: number;
  eligible_sample_ids: string[];
  skipped_samples: { sample_id: string; label: string; reasons: string[] }[];
};

export type MethodSourceReference = {
  path: string;
  label: string;
  kind: 'skill' | 'script' | 'command';
  line_start: number | null;
  line_end: number | null;
};

export type CommandReference = {
  command: string;
  language: string;
  source_path: string;
  line_start: number | null;
  kind: 'operation' | 'setup' | 'environment' | 'example';
  executable: boolean;
};

export type ExecutionRecipe = {
  recipe_id: string;
  label: string;
  operation_value: string;
  interface_capability: string;
  interface_method_id: string;
  exposed_parameters: string[];
  steps: Array<{
    id: string;
    title: string;
    capability: string;
    method_id: string;
    fixed_parameters: Record<string, ParameterValue>;
    parameter_bindings?: Record<string, string>;
    inherit_parameters?: boolean;
    workspace: string;
  }>;
};

export type MethodOption = {
  definition_id?: string; definition_version?: string; entry_kind?: 'method' | 'pipeline' | 'capability';
  id: string;
  skill_id: string;
  label: string;
  description: string;
  runner_id: string;
  execution_mode: 'planning' | 'runner';
  input_types: string[];
  output_types: string[];
  requires_features: string[];
  compatible: boolean;
  blocked_reasons: string[];
  input_warnings?: string[];
  scope_warnings?: string[];
  recommended: boolean;
  recommendation_reason: string;
  parameters: ParameterSpec[];
  input_slots?: InputSlotSpec[];
  output_slots?: OutputSlotSpec[];
  structure_level?: 'profile' | 'evidence' | 'reviewed';
  source_references?: MethodSourceReference[];
  command_references?: CommandReference[];
  execution_recipe?: ExecutionRecipe | null;
  included_capabilities?: string[];
  execution_resolution?: {
    state: string;
    reason: string;
    requirements?: string[];
    source_method_id?: string;
    target_method_id?: string;
  };
};

export type WorkflowStep = {
  input_suggestions?: Record<string, {path: string; relative_path: string; root_id: string | null; binding_mode: InputSelectionMode; source: string}>;
  id: string;
  order: number;
  title: string;
  description: string;
  capability: string;
  origin?: 'requested' | 'dependency';
  selection: 'auto' | 'user';
  depends_on: string[];
  status:
    | 'configured'
    | 'needs_method'
    | 'method_selected'
    | 'parameters_ready'
    | 'parameters_reviewed'
    | 'confirmed';
  method_options: MethodOption[];
  selected_method_id: string | null;
  selected_parameters: Record<string, ParameterValue>;
  input_bindings?: Record<string, InputBinding>;
  manual_input_types?: string[];
  missing_required_inputs?: string[];
  command_preview: string | null;
};

export type DryRunReport = {
  quality_checks?: Array<{status:string;code:string;slot_id:string;step_id:string;sample_id?:string;details:Record<string,unknown>}>;
  quality_summary?:Record<string,number>;
  quality_review_required?:boolean;
  quality_acknowledged?:boolean;
  valid: boolean;
  execution_enabled: boolean;
  dataset_path_validated: boolean;
  commands: Array<{
    step_id: string;
    runner_id: string;
    command_preview: string;
    execution_mode: 'planning' | 'runner';
    valid: boolean;
    messages: string[];
  }>;
  warnings: string[];
  checked_at: string;
};

export type InputSelectionMode = 'auto' | 'single' | 'per_sample' | 'template';

export type Workflow = {
  managed_task_id?: string | null;
  input_selection?: 'automatic' | 'user';
  output_layout?: 'legacy' | 'sample_steps';
  workspace_id?: string | null;
  workspace_root_path?: string | null;
  output_relative_path?: string;
  workflow_id: string;
  template_id: string;
  revision: number;
  title: string;
  summary: string;
  original_request: string;
  status: WorkflowStatus;
  dataset_id: string | null;
  dataset: Dataset | null;
  planner_source: string;
  execution_mode: 'planning' | 'runner' | 'mixed';
  plan_kind: 'atomic' | 'composite';
  requested_capabilities: string[];
  excluded_capabilities?: string[];
  input_capabilities?: string[];
  dependency_policy?: 'target_only' | 'complete';
  steps: WorkflowStep[];
  warnings: string[];
  can_lock: boolean;
  can_unlock: boolean;
  can_review_parameters: boolean;
  can_dry_run: boolean;
  can_confirm: boolean;
  locked_at: string | null;
  parameters_reviewed_at: string | null;
  dry_run_report: DryRunReport | null;
  sample_scope?: SampleScope | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ExecutionOptions = {
  output_layout?: 'legacy' | 'sample_steps' | null;
  backend: 'local' | 'slurm';
  partition: string;
  cpus: number;
  memory_gb: number;
  gpus: number;
  time_minutes: number;
  node?: string | null;
  nodes?: NodeAllocation[];
  sample_mode?: 'single' | 'per_sample';
};

export type NodeAllocation = {
  node: string;
  cpus: number;
  memory_gb: number;
  gpus: number;
  time_minutes: number;
  parallel_samples?: number;
};

export type SlurmNode = {
  name: string;
  partition: string;
  state: string;
  schedulable: boolean;
  cpus?: number;
  allocated_cpus?: number;
  memory_mb?: number;
  free_memory_mb?: number;
  gpus: number;
};

export type ExecutionBackends = {
  local: { available: boolean };
  slurm: {
    enabled: boolean;
    available: boolean;
    reason?: string;
    partitions: string[];
    gpu_available: boolean;
    nodes?: SlurmNode[];
  };
};

export type ExecutionDraft = {
  execution: ExecutionOptions | null;
  draft_revision: number;
  workflow_revision: number | null;
};

export const DEFAULT_EXECUTION_OPTIONS: ExecutionOptions = {
  backend: 'slurm',
  sample_mode: 'per_sample',
  node: null,
  nodes: [],
  partition: 'all',
  cpus: 4,
  memory_gb: 8,
  gpus: 0,
  time_minutes: 60
};

/** Competition workbench policy, including drafts saved before Slurm was fixed. */
export function competitionExecutionOptions(value: ExecutionOptions): ExecutionOptions {
  return {
    ...value,
    backend: 'slurm',
    sample_mode: 'per_sample',
    node: null,
    nodes: value.nodes?.length ? value.nodes : value.node ? [{
      node: value.node,
      cpus: value.cpus,
      memory_gb: value.memory_gb,
      gpus: value.gpus,
      time_minutes: value.time_minutes,
      parallel_samples: 1
    }] : []
  };
}

/** Ignore JSON property order and server-filled optional defaults when saving. */
export function executionOptionsFingerprint(value: ExecutionOptions): string {
  return JSON.stringify([
    value.backend, value.sample_mode ?? 'single', value.partition,
    value.cpus, value.memory_gb, value.gpus, value.time_minutes,
    value.output_layout ?? null, value.node ?? null,
    (value.nodes ?? []).map(node => [node.node, node.cpus, node.memory_gb,
      node.gpus, node.time_minutes, node.parallel_samples ?? 1])
  ]);
}
