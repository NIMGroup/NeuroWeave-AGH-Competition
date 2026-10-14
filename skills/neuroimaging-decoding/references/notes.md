# neuroimaging-decoding：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`neuroimaging-decoding` coordinates three complementary analyses:

| Mode | Input | Scientific output |
|---|---|---|
| `mvpa` | ROI/parcel feature CSV | cross-validated prediction |
| `roi-glm` | ROI feature CSV + design CSV | ROI-wise effect and FDR table |
| `searchlight` | aligned NIfTI images + mask | voxel-wise decoding map |

Use `nilearn-tool` for full first-level and second-level task-fMRI GLM design.
This skill handles the downstream ROI or SearchLight analysis.

### 1. ROI MVPA

The tabular estimator choices are inherited from `statistical-ml`. Scaling and
feature selection must remain inside cross-validation.

### 2. ROI-wise GLM

`roi_features.csv` contains subject ID plus ROI columns. `design.csv` contains
the same subject ID plus intercept/covariate/contrast columns.

The output includes effect, standard error, P value, and FDR-corrected Q value
for every ROI.

### 3. Voxel-wise SearchLight

Create `images.txt` with one aligned NIfTI path per line. The row order must
match the labels CSV.

All images and the mask must share the same space, affine, and voxel grid.

## Reference

- Nilearn provides the voxel-wise SearchLight implementation.
- The ROI GLM uses explicit design matrices and Benjamini-Hochberg correction.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
