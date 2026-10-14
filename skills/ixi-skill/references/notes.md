# ixi-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: ~600 healthy subjects
- **Sites**: 3 London hospitals with different scanners
  - **Hammersmith Hospital (HH)**: Philips 3T
  - **Guy's Hospital (Guy)**: Philips 1.5T
  - **Institute of Psychiatry (IOP)**: GE 1.5T
- **Modalities**: T1w, T2w, MRA (some subjects also have PD and DTI)
- **Format**: NIfTI (.nii.gz)
- **Access**: https://brain-development.org/ixi-dataset/

## Supported Modalities

| Modality | Description | Sites |
|---|---|---|
| T1w | High-resolution structural MRI | All 3 sites |
| T2w | T2-weighted structural MRI | All 3 sites |
| MRA | Magnetic Resonance Angiography | All 3 sites |
| PD | Proton Density (some subjects) | Selected sites |
| DTI | Diffusion Tensor Imaging (some subjects) | Selected sites |

## Site-Specific Scanner Information

| Site | Scanner | Field Strength | Notes |
|---|---|---|---|
| Hammersmith Hospital (HH) | Philips | 3T | Higher resolution, smaller voxel size |
| Guy's Hospital (Guy) | Philips | 1.5T | Standard resolution |
| Institute of Psychiatry (IOP) | GE | 1.5T | Standard resolution |

## Important Notes and Limitations

- IXI is a multi-site dataset with different scanners; consider site effects in analysis.
- Site information can be inferred from subject ID prefix (HH, Guy, IOP).
- MRA data is unique to IXI; vessel analysis requires specialized tools.
- Some subjects may have missing modalities (PD, DTI).
- `ixi-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- IXI Dataset: https://brain-development.org/ixi-dataset/
- Brain-development.org: https://brain-development.org/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 13:31 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
