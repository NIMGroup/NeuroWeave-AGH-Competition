# oasis-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **OASIS-1 (Cross-sectional)**: ~416 subjects aged 18-96
  - T1w MRI, some with very mild to mild AD
- **OASIS-2 (Longitudinal)**: ~150 subjects aged 60-96
  - Multiple sessions, non-demented and demented
- **OASIS-3**: ~1,000+ subjects, longitudinal spanning ~20 years
  - T1w, PET (amyloid, tau), ASL
- **Scanner**: 1.5T Siemens Vision
- **Clinical**: CDR (Clinical Dementia Rating), MMSE, demographics
- **Access**: OpenNeuro ds000014 (OASIS-1), www.oasis-brains.org (OASIS-3)
- **Format**: BIDS-compliant (OASIS-1), raw + derivatives (OASIS-3)

## Supported Modalities

| Modality | Description | Versions |
|---|---|---|
| T1w | High-resolution structural MRI | OASIS-1, 2, 3 |
| PET | Amyloid (PIB/AV45), Tau (AV1451) | OASIS-3 |
| ASL | Arterial spin labeling perfusion | OASIS-3 |
| fMRI | Functional MRI | OASIS-3 (limited) |

## OASIS Clinical Measures

| Measure | Description | Range |
|---|---|---|
| CDR | Clinical Dementia Rating | 0 (normal), 0.5 (very mild), 1 (mild), 2 (moderate), 3 (severe) |
| MMSE | Mini-Mental State Examination | 0-30 (higher = better) |
| Age | Age at scan | 18-96 years |
| Sex | Biological sex | M/F |
| SES | Socioeconomic status | 1-5 |

## Important Notes and Limitations

- OASIS-1 is the most commonly used version; OASIS-3 adds PET and longitudinal data.
- Age range is very wide (18-96); analyses should account for age effects.
- CDR is the primary dementia staging tool; MMSE provides additional cognitive screening.
- OASIS-1 uses 1.5T scanner; resolution is lower than modern 3T/7T datasets.
- `oasis-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- OASIS: https://www.oasis-brains.org/
- Marcus et al. (2007): Open Access Series of Imaging Studies (OASIS). Journal of Cognitive Neuroscience.
- OpenNeuro ds000014

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 13:55 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
