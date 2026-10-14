# statistical-ml：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`statistical-ml` is the reference implementation for classical prediction
and inference on subject-level tabular features. It keeps imputation, scaling,
feature selection, and model fitting inside each training fold.

**Supported estimators**

| Task | Models |
|---|---|
| Classification | `logistic`, `ridge`, `elastic_net`, `svm`, `xgboost` |
| Regression | `ols`, `ridge`, `elastic_net`, `svr`, `xgboost` |
| Inference | Cohen's d, robust formula OLS/GLM, linear mixed-effects models |
| Longitudinal treatment analysis | dose/time/treatment mixed-effects formulas |

Use this skill after imaging data have been converted into one row per subject
or observation. Use `temporal-models` for sequence tensors and
`cnn3d` for full 3D volumes.

### 1. Prepare a tabular CSV

The CSV must contain a subject identifier, target, and numeric features:

```text
subject_id,site,diagnosis,age,roi_001,roi_002,network_fc
sub-001,A,0,24,0.12,-0.04,0.31
sub-002,B,1,31,0.08,-0.09,0.27
```

Identifier, target, and optional group columns are excluded from predictors.

### 2. Classification

Use `--group-col site` or another cohort column when sites must not be split
between training and test folds.

### 5. Formula-based inference

Use the Python APIs in `models/statistical_ml/mixed_effects.py` for robust OLS,
mixed-effects, and dose-response analyses. Report the formula, grouping
variable, effect estimate, confidence interval, and multiplicity correction.

## Reference

- Scikit-learn pipelines are used to prevent preprocessing leakage.
- Statsmodels provides formula-based OLS/GLM and mixed-effects inference.
- XGBoost remains optional and is loaded only when requested.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
