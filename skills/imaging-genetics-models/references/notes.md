# imaging-genetics-models：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`imaging-genetics-models` provides matrix-based association, polygenic scoring,
and multivariate genotype-imaging analysis. It also builds explicit PLINK2
commands without embedding or redistributing the external executable.

**Supported modes**

| Mode | Required arrays | Output |
|---|---|---|
| `association` | `genotype`, `phenotype` | covariate-adjusted variant tests |
| `lmm` | above plus `kinship` | kinship-aware variant tests |
| `prs` | `genotype`, `weights` | subject polygenic score |
| `pls` | `X`, `Y` | paired latent components |
| `cca` | `X`, `Y` | canonical variates |

Population structure, ancestry, batch, age, sex, site, and relatedness must be
handled before genetic effects are interpreted.

### 1. Variant-imaging association

Create an NPZ bundle:

```text
genotype:   [subjects, variants]
phenotype:  [subjects] or [subjects, phenotypes]
variant_id: [variants] (optional)
covariates: [subjects, covariates] (optional)
kinship:    [subjects, subjects] (required only for `lmm`)
```

Use `--model lmm` when the bundle contains a kinship matrix.

### 2. Polygenic score

```text
genotype:   [subjects, variants]
weights:    [variants]
subject_id: [subjects] (optional)
```

### 3. PLS or CCA

```text
X: [subjects, genetic features]
Y: [subjects, imaging phenotypes]
```

Fit dimensionality reduction and covariate residualization inside the training
data when the analysis is evaluated predictively.

## Reference

- PLINK2 remains an external executable with independent installation terms.
- Association outputs include effect estimates and multiplicity-ready P values.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
