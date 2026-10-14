# nifd-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: ~120 participants
  - **bvFTD**: Behavioral variant frontotemporal dementia
  - **svPPA**: Semantic variant primary progressive aphasia
  - **nfvPPA**: Nonfluent variant primary progressive aphasia
  - **Healthy controls**: Age-matched
- **Scanner**: 3T Siemens TIM Trio
- **Modalities**: T1w sMRI, rs-fMRI, dMRI/DTI
- **Clinical**: CDR, MMSE, neuropsychological battery
- **Access**: Controlled download through the NIFD/FTLDNI project in LONI IDA
- **Format**: IDA export; BIDS staging and validation are required before BIDS-native tools

## Supported Modalities

| Modality | Description | Details |
|---|---|---|
| T1w | High-resolution structural MRI | 1mm isotropic, cortical thickness/atrophy |
| rs-fMRI | Resting-state functional MRI | Functional connectivity, network degeneration |
| dMRI | Diffusion-weighted imaging | DTI, white matter tract integrity |

## NIFD Diagnostic Groups

| Group | Description | Typical N |
|---|---|---|
| bvFTD | Behavioral variant FTD | ~40 |
| svPPA | Semantic variant PPA | ~20 |
| nfvPPA | Nonfluent variant PPA | ~15 |
| Control | Healthy age-matched controls | ~45 |

## Important Notes and Limitations

- NIFD is a clinical cohort; patient data requires careful handling.
- NIFD data may not be redistributed; preserve the DUA, required acknowledgement, release/export date, and local access controls.
- Diagnostic groups (bvFTD, svPPA, nfvPPA) have distinct atrophy patterns; group-level analyses should account for heterogeneity.
- Cortical thickness and voxel-based morphometry are commonly used structural measures.
- Network degeneration hypothesis: FTD targets specific large-scale networks.
- `nifd-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- NIFD/FTLDNI LONI DUA: https://ida.loni.usc.edu/collaboration/access/appApply.jsp?project=NIFD
- NIFD: UCSF Memory and Aging Center
- Frontotemporal Dementia: FTDC clinical diagnostic criteria

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-08-11 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
