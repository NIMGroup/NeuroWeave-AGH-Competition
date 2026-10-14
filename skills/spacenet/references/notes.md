# spacenet：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## When to Use SpaceNet

- The user wants classical disease classification instead of a deep learning model.
- The dataset size is moderate and model interpretability matters.
- The user wants voxel-wise discriminative maps and sparse spatial regularization.
- The task is case-control prediction, diagnosis support, or cross-validated disease discrimination.

## Limitations and Notes

- SpaceNet requires well-aligned images in a common space and can be computationally heavier than ROI-based methods.
- Site effects and confounds can dominate disease classification if not controlled properly.
- Small sample sizes can lead to optimistic estimates unless split strategy is rigorously managed.

## Reference

- Varoquaux G, Gramfort A, Poline JB, Thirion B. Brain covariance selection: better individual functional connectivity models using population prior.
- Nilearn decoding documentation: https://nilearn.github.io/stable/decoding/index.html

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-04-14 00:45 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
