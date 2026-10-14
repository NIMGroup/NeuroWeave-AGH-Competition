# ppmi-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: ~2,000+ participants
  - **PD patients**: Parkinson's disease (early stage, drug-naive)
  - **Prodromal**: REM sleep behavior disorder, hyposmia
  - **Healthy controls**: Age-matched
- **Scanner**: 3T Siemens (multi-site)
- **Modalities**: T1w sMRI, rs-fMRI, dMRI/DTI, DaTscan SPECT
- **Clinical**: MDS-UPDRS, MoCA, UPSIT, REM sleep, DAT imaging
- **Access**: Controlled PPMI project in LONI IDA after DUA and application approval
- **Format**: IDA export; use a documented community conversion or local BIDS staging before BIDS-native tools
- **Reference**: Marek et al. (2011), Lancet Neurology

## Supported Modalities

| Modality | Description | Details |
|---|---|---|
| T1w | High-resolution structural MRI | 1mm isotropic, substantia nigra volumetry |
| rs-fMRI | Resting-state functional MRI | Basal ganglia connectivity |
| dMRI | Diffusion-weighted imaging | DTI, nigrostriatal tract integrity |
| DaTscan | SPECT dopamine transporter | Striatal binding ratios |

## PPMI Clinical Measures

| Measure | Description | Domain |
|---|---|---|
| MDS-UPDRS | Movement Disorder Society Unified PD Rating Scale | Motor function |
| MoCA | Montreal Cognitive Assessment | Global cognition |
| UPSIT | University of Pennsylvania Smell Identification Test | Olfaction |
| RBD | REM Sleep Behavior Disorder screening | Sleep |
| H&Y | Hoehn and Yahr staging | Disease stage |
| DAT | Dopamine transporter binding (SPECT) | Dopaminergic function |

## Important Notes and Limitations

- PPMI is controlled data and must not be redistributed. Preserve the governing DUA and required publication/ongoing-analysis obligations with project provenance.
- PPMI is a multi-site study; site effects should be modeled in group analyses.
- Early-stage PD patients are often drug-naive, which is valuable for studying untreated disease.
- DaTscan SPECT provides dopaminergic imaging but may not follow standard BIDS conventions.
- Longitudinal design enables progression modeling.
- Large sample size and rich clinical phenotyping make PPMI ideal for biomarker discovery.
- `ppmi-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- PPMI data access: https://www.ppmi-info.org/access-data-specimens/download-data
- PPMI: https://www.ppmi-info.org/
- Marek et al. (2011): The Parkinson Progression Marker Initiative (PPMI). Lancet Neurology.
- LONI IDA: https://ida.loni.usc.edu/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-08-11 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
