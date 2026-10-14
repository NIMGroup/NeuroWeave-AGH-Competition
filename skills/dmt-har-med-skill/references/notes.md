# dmt-har-med-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
DMT-HAR-MED data is available on OpenNeuro:
- Website: https://openneuro.org/datasets/ds006644/versions/1.0.1

### Supported DMT-HAR-MED Data Packages
- **Imaging data**: rs-fMRI (NIfTI format)
- **Phenotype data**: intervention conditions, behavioral and physiological measurements
- **Participants**: 40 participants with psychedelic intervention

## Important Notes and Limitations

- DMT-HAR-MED is a specialized psychedelic intervention dataset; analysis requires domain expertise.
- DMT-HAR-MED data from OpenNeuro is already in BIDS format; re-staging may not be needed.
- The dataset includes multiple experimental conditions; event files define condition timing.
- DMT-HAR-MED includes only rs-fMRI; no structural (T1w) or diffusion data is available.
- `dmt-har-med-skill` is orchestration-only; detailed preprocessing logic remains in `fmri-skill`.

## Reference

- DMT-HAR-MED: https://openneuro.org/datasets/ds006644/versions/1.0.1
- BIDS spec: https://bids.neuroimaging.io/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 01:56 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
