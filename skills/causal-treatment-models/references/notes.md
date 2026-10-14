# causal-treatment-models：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`causal-treatment-models` estimates average or conditional treatment effects
from observational subject-level features. It is for the contrast
`Y(1) - Y(0)`, not for predicting the observed outcome alone.

**Supported estimators**

| Model | Output |
|---|---|
| `ipw` | propensity-weighted ATE as constant CATE |
| `s_learner` | single outcome model treatment contrast |
| `t_learner` | separate treated/control outcome models |
| `x_learner` | imputed effects blended by propensity |
| `doubly_robust` | doubly robust pseudo-outcome CATE |
| `policy_learner` | interpretable treatment assignment policy |
| `causal_forest` | `econml` CausalForestDML |
| `tarnet` | shared representation with two outcome heads |
| `dragonnet` | TARNet plus propensity head |

The CLI uses cross-fitted held-out predictions. Causal interpretation still
requires consistency, positivity, no unmeasured confounding, and a defensible
temporal ordering.

### 1. Prepare treatment data

```text
subject_id,treatment,response,age,sex,baseline_score,roi_001
sub-001,1,4.2,64,0,18.0,0.12
sub-002,0,1.7,59,1,17.5,0.08
```

Treatment must be binary for the current CLI. Include only pretreatment
covariates in the feature matrix.

### 4. Causal Forest or policy learning

Use `--model causal_forest` for nonlinear heterogeneous effects and
`--model policy_learner` for a compact treatment rule. Report overlap,
propensity distributions, standardized mean differences, ATE/CATE uncertainty,
and policy value under an explicit evaluation design.

## Reference

- Shalit et al. representation learning for individual treatment effects (2017).
- Shi et al. DragonNet for targeted regularization (2019).
- `econml` provides the optional CausalForestDML backend.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
