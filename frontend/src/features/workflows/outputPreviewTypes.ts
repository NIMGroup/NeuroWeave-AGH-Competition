export type PreviewOutput = {
  slot_id: string; label: string; artifact_type: string; format: string;
  relative_path: string | null; certainty: 'declared' | 'dynamic_name' | 'type_only';
};
export type OutputPreview = {
  workflow_id: string; revision: number; output_layout: 'legacy' | 'sample_steps';
  sample_mode: 'single' | 'per_sample'; executed: false; filesystem_checked: false;
  requested_sample_mode: 'single' | 'per_sample'; settings_need_change: boolean;
  naming_rule: 'sample_id' | 'ordinal_step' | 'step_only';
  run_id_assigned: false; status: 'available' | 'incomplete'; root_template: string | null;
  sample_count: number; excluded_sample_count: number; unit_count: number;
  declared_output_count: number; offset: number; next_offset: number | null;
  entries_truncated: boolean; issues: string[];
  groups: { sample_id: string | null; sample_label: string;
    source_relative_root: string | null; output_sample_directory: string | null;
    folder_matches_input_name: boolean | null; sample_id_sanitized: boolean;
    steps: { step_id: string; title: string; relative_directory: string; outputs: PreviewOutput[] }[] }[];
};
