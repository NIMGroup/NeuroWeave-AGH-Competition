# ukb-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Included
- Brain-related UK Biobank disease endpoint analysis using existing tables
- Cognitive and brain-health phenotype modeling
- Brain MRI derived phenotype association analysis
- Survival / logistic / linear modeling on UK Biobank-derived tables
- Sensitivity analysis, subgroup analysis, propensity score analysis, and mediation analysis
- Machine learning on brain-related tabular or derived neuroimaging features
- Visualization and manuscript-ready result summarization

### Excluded
- UK Biobank RAP access setup
- UK Biobank application, governance, or approval procedures
- Downloading demographics, proteomics, metabolomics, or raw UKB exports
- Cloud-side field selection or export helper workflows
- Low-level data download scripts

If the user asks how to obtain or download UK Biobank data, state that this skill only supports post-extraction analysis after the data has already been exported or derived.

## Brain-Relevant UKB Targets

This skill is intentionally restricted to brain-related UK Biobank content. Typical targets include:
- neurodegenerative outcomes: dementia, Alzheimer's disease, Parkinson's disease
- cerebrovascular outcomes: stroke and vascular brain-health outcomes
- cognition: memory, reaction time, executive function, fluid intelligence, cognitive decline
- psychiatric or brain-health outcomes when explicitly tied to brain-focused analysis
- imaging-derived brain phenotypes: cortical thickness, regional volume, subcortical volume, WMH burden, diffusion metrics, functional connectivity summaries

If the request is mainly non-brain UK Biobank epidemiology, this skill should state that it is out of scope.

## Important Notes & Limitations

- This skill assumes that the user already has legal access to UK Biobank data and has already exported or derived the needed local analysis files.
- This skill does not define fixed UKB field IDs because field selection differs across studies and exports.
- This skill should not guess disease definitions, date columns, or censoring rules when they are not provided.
- Sensitivity analysis should be framed as filtered-data reruns of the same main model, not as unrelated new pipelines.
- If multiple candidate outcomes or feature tables exist, the skill should first ask which one is primary before execution.
- This skill is intended for research workflows and not for clinical decision-making.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-05-06 15:12 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
