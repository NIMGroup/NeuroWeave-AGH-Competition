# cnn3d：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`cnn3d` is the reference compact residual 3D CNN. It owns one model,
one NPZ input contract, and one checkpoint format. NeuroSTORM remains a
separate model skill with its own external repository and runtime.

| Model | Input | Tasks |
|---|---|---|
| `VoxelCNN3D` | whole-volume tensor | classification, regression |

### 1. Prepare a volume NPZ

```text
X:          float array [subjects, channels, depth, height, width]
y:          array [subjects]
subject_id: string array [subjects] (optional)
```

All subjects must use the same orientation, voxel size, grid, crop, and
intensity-normalization protocol.

### 3. Regression

Preprocessing must be frozen before cross-validation. Site harmonization,
augmentation, and intensity transforms must not use held-out subjects.

## Reference

- Use `neurostorm` instead when the request explicitly targets NeuroSTORM,
  SwiFT, or the upstream multi-model fMRI platform.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
