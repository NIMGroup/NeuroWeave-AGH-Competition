import { request } from '../../api/http';

export type Skill = {
  name: string;
  display_name: string;
  description: string;
  layer: string;
  skill_type: string;
  runtime: string;
  status: string;
  summary_en?: string;
  summary_zh?: string;
  dependencies?: string[];
  source?: 'NeuroClaw' | 'LocalFlow' | 'NeuroWeave';
  script_count?: number;
  has_handler?: boolean;
  executable?: boolean;
  structure_mode?: 'advisory' | 'planning' | 'runner';
  runner_count?: number;
  ready_runner_count?: number;
  planning_method_count?: number;
  execution_state?: 'ready' | 'needs_environment' | 'planning';
  runtime_reasons?: string[];
};

export type CatalogStats = {
  total: number;
  sources: Record<string, number>;
  with_scripts: number;
  with_handlers: number;
  structured?: number;
  planning_structured?: number;
  runner_structured?: number;
  capability_catalog?: {
    capabilities: number;
    methods: number;
    planning_methods: number;
    runner_methods: number;
    evidence_backed_methods: number;
    reference_commands: number;
    artifact_types: number;
  };
  execution_enabled: boolean;
};

export type SkillsResponse = {
  skills: Skill[];
  stats?: CatalogStats | null;
};

export function fetchSkills(signal?: AbortSignal): Promise<SkillsResponse> {
  return request<SkillsResponse>('/api/skills', { signal });
}

export type SkillDetails = Skill & {
  instructions: string;
  orchestration?: {applicability?:string[]; limitations?:string[]; examples?:Array<{request:string;decision:string}>; description:string; selection_rules:string[]; functions:Array<{label:string;description?:string;role?:string}>};
  files: string[];
  complementary_skills?: string[];
};

export function fetchSkill(name: string, signal?: AbortSignal): Promise<SkillDetails> {
  return request<SkillDetails>(`/api/skills/${encodeURIComponent(name)}`, { signal });
}

export type SkillMethodSummary = {
  entry_kind?:'method'|'pipeline'|'capability'; definition_id?:string; role?:string;
  capability:string; method_id:string; skill_id:string; label:string; description:string;
  runner_id:string; execution_mode:'runner'|'planning'; parameter_count:number; input_count:number; output_count:number;
};
export type SkillMethodPage = {items:SkillMethodSummary[]; total:number; has_more:boolean; next_cursor:string|null};
export type SkillMethodDetail = SkillMethodSummary & {
  verification?:import('./MethodVerification').Verification;
  definition_revision:string; definition_version?:string;
  steps?:Array<{id:string;title:string;method_ref:string;method_version:string;fixed_parameters:Record<string,unknown>;parameter_bindings:Record<string,string>;input_bindings:Record<string,unknown>;when?:Record<string,unknown>}>;
  output_bindings?:Record<string,unknown>;
  runtime_status:{available:boolean;reason?:string};
  parameters:import('../workflows/workflowTypes').ParameterSpec[];
  input_slots:import('../workflows/workflowTypes').InputSlotSpec[];
  output_slots:import('../workflows/workflowTypes').OutputSlotSpec[];
  source_references:import('../workflows/workflowTypes').MethodSourceReference[];
  command_coverage?:{total:number|null;exposed:number;omitted:string[]};
};
export function fetchSkillMethods(skill:string,q:string,cursor:string,signal?:AbortSignal,kind='method') {
  return request<SkillMethodPage>(`/api/skills/${encodeURIComponent(skill)}/methods?${new URLSearchParams({q,cursor,kind,limit:'20'})}`,{signal});
}
export function fetchSkillMethod(kind:string,method:string,signal?:AbortSignal) {
  return request<SkillMethodDetail>(`/api/${kind === 'pipeline' ? 'pipelines' : 'methods'}/${encodeURIComponent(method)}`,{signal});
}
