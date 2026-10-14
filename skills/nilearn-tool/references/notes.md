# nilearn-tool：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### ✅ This tool does
- Load BOLD NIfTI and (optional) brain mask.
- Load fMRIPrep confounds TSV and apply common denoising regressors.
- Extract ROI time series from an atlas/parcellation.
- Compute ROI-to-ROI functional connectivity matrices.
- Compute seed-to-voxel connectivity maps.
- (Optional) Run first-/second-level GLM when events/maps are provided.

### ❌ This tool does NOT do
- Raw fMRI preprocessing (slice timing, motion correction, susceptibility distortion correction, eddy/topup, etc.).
  Those belong to `fmriprep-tool`, `hcppipeline-tool`, `fsl-tool`.

## Reference

- Nilearn documentation: https://nilearn.github.io/
- fMRIPrep confounds interface: Nilearn `nilearn.interfaces.fmriprep`
- Curated code snippets in this skill:
  - `skills/nilearn-tool/scripts/preprocess_bold_reference.py`
  - `skills/nilearn-tool/scripts/connectome_reference.py`
  - `skills/nilearn-tool/scripts/zalff_summary_reference.py`
  - `skills/nilearn-tool/scripts/task_glm_reference.py`
  - `skills/nilearn-tool/scripts/second_level_glm_reference.py`
  - `skills/nilearn-tool/scripts/rest_ica_reference.py`
  - `skills/nilearn-tool/scripts/rest_dictlearning_reference.py`
  - `skills/nilearn-tool/scripts/svm_classifier_reference.py`
  - `skills/nilearn-tool/scripts/spacenet_classifier_reference.py`
  - `skills/nilearn-tool/scripts/kmeans_parcellation_reference.py`
  - `skills/nilearn-tool/scripts/hierarchical_parcellation_reference.py`
  - `skills/nilearn-tool/scripts/denoise_timeseries_reference.py`

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-04-14 00:26 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
