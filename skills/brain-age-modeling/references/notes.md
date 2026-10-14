# brain-age-modeling：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`brain-age-modeling` is a leakage-safe task workflow over regression
estimators. It trains predicted-age models, fits age-bias correction on each
training fold, and exports held-out raw age, corrected age, and Brain-PAD.

```text
Brain-PAD = bias-corrected predicted age - chronological age
```

Positive Brain-PAD indicates an older-appearing brain relative to chronological
age under the fitted model; it is not by itself a diagnosis or causal effect.

### 1. Prepare brain features

```text
subject_id,site,age,cortical_thickness,hippocampal_volume,fc_001
sub-001,A,64,2.51,3810,0.12
sub-002,B,59,2.63,4022,0.08
```

Use a healthy training reference when the scientific interpretation requires
deviation from normative aging. Do not include downstream disease outcomes as
predictors.

### 3. Alternative regressors

The workflow reuses regression estimators from `statistical-ml`, including
`ols`, `ridge`, `elastic_net`, `svr`, and optional `xgboost`. Keep site,
family, or cohort groups intact where appropriate.

### 4. Downstream analysis

After held-out Brain-PAD has been generated, analyze group differences or
clinical associations with explicit age, sex, site, intracranial-volume, and
other prespecified covariates. Use only held-out Brain-PAD values.

## Reference

- Brain-PAD should always be reported together with the training population,
  feature family, validation design, and bias-correction procedure.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
