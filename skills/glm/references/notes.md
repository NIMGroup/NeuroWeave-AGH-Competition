# glm：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## When to Use GLM Instead of Deep Learning

- The user wants classical task activation analysis rather than phenotype prediction.
- The goal is statistical inference on task conditions or contrasts.
- The user wants group-level inference across subjects rather than individual-level prediction.
- Sample size is limited and interpretability of condition effects is more important than representation learning.
- The required output is a contrast map, z map, or cluster-level inference report.

## Limitations and Notes

- GLM is primarily for task-fMRI, not resting-state phenotype modeling.
- Results are sensitive to event timing quality, motion confounds, and preprocessing decisions.
- Group-level inference requires consistent first-level contrast definitions across subjects.
- Second-level GLM requires aligned subject-level maps and a valid group design matrix.

## Reference

- Friston KJ et al. Statistical Parametric Mapping foundations for task-fMRI analysis.
- Nilearn GLM documentation: https://nilearn.github.io/stable/glm/index.html

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-04-14 00:28 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
