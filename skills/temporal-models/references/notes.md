# temporal-models：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`temporal-models` trains sequence encoders on ordered neuroimaging or clinical
measurements. It supports variable sequence lengths and estimates normalization
statistics from training subjects only.

**Supported models**

| Model | Encoder | Typical use |
|---|---|---|
| `lstm` | long short-term memory | longitudinal visits |
| `gru` | gated recurrent unit | compact recurrent baseline |
| `tcn` | temporal convolutional network | local temporal patterns |
| `transformer` | masked temporal self-attention | longer dependencies |

Both `classification` and `regression` are supported.

### 1. Prepare an NPZ sequence bundle

```text
X:          float array [subjects, time, features]
y:          array [subjects]
lengths:    integer array [subjects] (optional)
subject_id: string array [subjects] (optional)
```

Padded frames must occur after each valid sequence. If `lengths` is absent,
every sequence is treated as fully valid.

### 3. Regression with temporal Transformer

Use subject-level folds; never split frames or visits from one subject across
training and test sets.

## Reference

- Sequence masking is driven by the optional `lengths` array.
- Scaling is fitted from valid training frames and padding is restored to zero.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
