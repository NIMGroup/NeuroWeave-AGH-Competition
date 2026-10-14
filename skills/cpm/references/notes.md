# cpm：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`cpm` is the reference implementation of Connectome Predictive
Modeling. Edge selection is repeated independently inside every training fold.

| Task | Input | Output |
|---|---|---|
| Classification | subject FC matrices/vectors | class and probability |
| Regression | subject FC matrices/vectors | continuous prediction |

### 1. Prepare data

`connectomes.npz`:

```text
X:          [subjects, nodes, nodes] or [subjects, edges]
subject_id: [subjects]
```

`labels.csv` contains the same subject IDs and a target column.

### 3. Classification

If `p-threshold` is tuned, use nested validation or training-only selection.

## Reference

- Finn et al. functional connectome fingerprinting and connectome-based
  prediction framework, Nature Neuroscience (2015).

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
