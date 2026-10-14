# adhd200-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
ADHD-200 data is distributed through the **FCP/INDI** repository:
- Website: https://fcon_1000.projects.nitrc.org/indi/adhd200/

### Supported ADHD-200 Data Packages
- **Imaging data**: T1w, rs-fMRI (NIfTI format) from 8 imaging sites
- **Phenotype data**: CSV files with diagnosis, ADHD measures, demographics, medication history, QC measures
- **Sites**: Peking, Brown, NYU, KKI, NeuroImage, OHSU, Pitt, Washington University

## Important Notes and Limitations

- ADHD-200 has heterogeneous acquisition parameters across 8 sites; site effects must be addressed in analysis.
- ADHD-200 subject IDs are numeric and vary in length across sites.
- Diagnosis labels vary by site (ADHD-combined, ADHD-inattentive, ADHD-hyperactive, typically developing).
- ADHD-200 data does not include task-fMRI; only resting-state fMRI is available.
- `adhd200-skill` is orchestration-only; detailed preprocessing logic remains in `smri-skill` and `fmri-skill`.

## Reference

- ADHD-200: https://fcon_1000.projects.nitrc.org/indi/adhd200/
- BIDS spec: https://bids.neuroimaging.io/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 01:50 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
