# hbn-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
HBN data is distributed through the **FCP/INDI** repository:
- Website: https://fcon_1000.projects.nitrc.org/indi/cmi_healthy_brain_network/

### Supported HBN Data Packages
- **Imaging data**: T1w, T2w, dMRI, rs-fMRI, task-fMRI (NIfTI format)
- **EEG data**: resting-state and task EEG recordings
- **Phenotype data**: CSV/TSV files with psychiatric, behavioral, cognitive, lifestyle, genetics, actigraphy measures
- **Sites**: Rutgers University Brain Imaging Center (primary), with additional sites planned

## Important Notes and Limitations

- HBN is a pediatric/adolescent cohort (ages 5-21); age-appropriate processing parameters may be needed.
- HBN includes EEG data in addition to standard neuroimaging modalities.
- HBN data is released in waves; not all subjects have all modalities.
- HBN subject IDs use NDAR format (e.g., `NDARAA075AMK`).
- `hbn-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- HBN: https://fcon_1000.projects.nitrc.org/indi/cmi_healthy_brain_network/
- BIDS spec: https://bids.neuroimaging.io/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 10:49 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
