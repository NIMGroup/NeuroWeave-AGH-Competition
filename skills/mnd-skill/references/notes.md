# mnd-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: 59 participants
  - **Patient group**: Motor Neuron Disease (e.g., ALS) patients
  - **Control group**: Healthy age-matched controls
- **Modalities**: rs-fMRI, task-fMRI (motor tasks)
- **Format**: BIDS-compliant
- **Access**: OpenNeuro ds005874
- **License**: CC0

## Supported Modalities

| Modality | Description | Tasks/Conditions |
|---|---|---|
| rs-fMRI | Resting-state functional MRI | Eyes open/closed |
| task-fMRI | Task-based functional MRI | Motor tasks (finger tapping, hand grip) |

## MND Task Paradigms

| Task | Description | Duration |
|---|---|---|
| REST | Resting-state (eyes open/closed) | ~8 min |
| MOTOR | Motor tasks (finger tapping, hand grip) | ~5 min |

## Important Notes and Limitations

- MND is a clinical cohort; patient data requires careful handling.
- Case-control matching should be verified before group comparisons.
- Motor tasks are designed to probe motor network function; standard motor task GLM applies.
- The dataset is relatively small (59 participants); statistical power may be limited.
- `mnd-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- OpenNeuro ds005874: https://openneuro.org/datasets/ds005874

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 13:31 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
