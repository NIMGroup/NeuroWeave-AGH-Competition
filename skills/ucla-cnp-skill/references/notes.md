# ucla-cnp-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: ~270 participants
  - **Healthy controls**: Age-matched
  - **ADHD**: Attention deficit hyperactivity disorder
  - **Bipolar disorder**: Bipolar I/II
  - **Schizophrenia**: Schizophrenia spectrum
- **Scanner**: 3T Siemens TIM Trio (UCLA)
- **Modalities**: T1w sMRI, task-fMRI, dMRI/DTI
- **Tasks**: Multiple cognitive tasks (stop-signal, spatial working memory, etc.)
- **Clinical**: Extensive cognitive battery, diagnostic assessments
- **Access**: OpenNeuro ds000030
- **Format**: BIDS-compliant
- **Reference**: Poldrack et al. (2016), Scientific Data

## Supported Modalities

| Modality | Description | Tasks/Conditions |
|---|---|---|
| T1w | High-resolution structural MRI | 1mm isotropic |
| task-fMRI | Task-based functional MRI | Stop-signal, spatial WM, face memory, etc. |
| dMRI | Diffusion-weighted imaging | DTI, white matter tractography |

## UCLA CNP Task Paradigms

| Task | Description | Cognitive Domain |
|---|---|---|
| Stop-signal | Response inhibition | Executive function |
| Spatial working memory | Spatial WM maintenance | Working memory |
| Face memory | Face encoding/retrieval | Episodic memory |
| Balloon analog risk | Risk taking behavior | Decision making |
| Monetary incentive | Reward processing | Motivation |

## UCLA CNP Diagnostic Groups

| Group | Description | Typical N |
|---|---|---|
| Control | Healthy age-matched | ~130 |
| ADHD | Attention deficit | ~50 |
| Bipolar | Bipolar disorder | ~45 |
| Schizophrenia | Schizophrenia spectrum | ~45 |

## Important Notes and Limitations

- UCLA CNP has 4 diagnostic groups; group comparisons should account for sample size differences.
- Multiple cognitive tasks enable rich cognitive phenotyping.
- OpenNeuro ds000030 is a widely used benchmark dataset for BIDS tools.
- Extensive cognitive battery enables linking brain structure/function to behavior.
- `ucla-cnp-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- UCLA CNP: https://www.humanconnectome.org/
- Poldrack et al. (2016): A phenomic analysis of the UCLA Consortium for Neuropsychiatric Phenomics LA5c Study. Scientific Data.
- OpenNeuro ds000030

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 14:21 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
