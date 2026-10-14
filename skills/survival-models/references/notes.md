# survival-models：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`survival-models` trains censor-aware prognosis models from subject-level
features. Every sample requires a follow-up duration and an event indicator;
censored observations must not be converted into ordinary regression labels.

**Supported models**

| Model | Implementation | Dependency |
|---|---|---|
| `cox` | proportional hazards baseline | core |
| `rsf` | Random Survival Forest | `scikit-survival` |
| `deepsurv` | neural Cox risk model | PyTorch |
| `xgboost_survival` | XGBoost `survival:cox` | `xgboost` |

The primary metric is Harrell's concordance index. The exported prediction is
a relative risk score, not an absolute probability unless separately
calibrated at a specified time horizon.

### 1. Prepare survival data

```text
subject_id,site,followup_days,progressed,roi_001,roi_002,age
sub-001,A,730,1,0.12,-0.04,64
sub-002,B,910,0,0.08,-0.09,59
```

`event-col` must be binary (`1` observed event, `0` censored). Duration must be
positive and use one consistent unit.

### 4. Tree-based survival models

Set `--model rsf` or `--model xgboost_survival`. Use grouped folds for
multi-site cohorts and report event counts per fold in addition to sample
counts.

## Reference

- Katzman et al. DeepSurv, BMC Medical Research Methodology (2018).
- Random Survival Forest and XGBoost are optional third-party backends.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
