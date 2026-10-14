# tcp-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: ~600+ participants
  - Transdiagnostic approach: participants span multiple diagnostic categories
  - **Healthy controls**: Age-matched
  - **Psychiatric groups**: Depression, anxiety, psychosis spectrum, etc.
- **Scanner**: 3T Siemens (WashU)
- **Modalities**: T1w sMRI, rs-fMRI, dMRI/DTI
- **Clinical**: RDoC-informed dimensional measures, diagnostic assessments
- **Access**: NIMH Data Archive (NDA), OpenNeuro
- **Format**: BIDS-compliant
- **Reference**: Barch, Gordon et al., WashU

## Supported Modalities

| Modality | Description | Details |
|---|---|---|
| T1w | High-resolution structural MRI | 1mm isotropic, cortical thickness |
| rs-fMRI | Resting-state functional MRI | Eyes open, functional connectivity |
| dMRI | Diffusion-weighted imaging | DTI, white matter tractography |

## TCP Clinical Dimensions

| Domain | Measures | RDoC Construct |
|---|---|---|
| Negative valence | Anhedonia, anxiety | Negative valence systems |
| Positive valence | Reward processing | Positive valence systems |
| Cognitive | Working memory, executive function | Cognitive systems |
| Social | Social cognition | Social processes |
| Arousal | Arousal/regulatory systems | Arousal/regulatory systems |

## Important Notes and Limitations

- TCP uses a transdiagnostic approach; analyses should consider dimensional rather than categorical models.
- RDoC-informed phenotyping enables cross-diagnostic connectivity analyses.
- Connectome-based predictive modeling (CPM) is a commonly used analysis approach.
- Multi-diagnostic design requires careful handling of group comparisons.
- `tcp-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- TCP: Washington University in St. Louis
- Barch, Gordon et al.: Transdiagnostic Connectome Project
- NIMH Data Archive: https://nda.nih.gov/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 14:21 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
