# adni-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes and Limitations

- ADNI and ADNI-DOD are controlled-access projects; do not redistribute source data or restricted derivatives.
- Confirm the selected project before querying because ADNI-DOD uses different cohort tables and identifiers from standard ADNI.
- ADNI subject naming must be normalized (e.g., `130_S_0969` -> `130S0969`).
- fMRIPrep requires FreeSurfer license and sufficient disk space.
- DK68 pipeline assumes `aparc+aseg.mgz` is available in fMRIPrep outputs.
- VQA scripts assume a subject-per-folder layout; adapt scripts if your ADNI organization differs.

## Reference

- ADNI data access: https://adni.loni.usc.edu/data-samples/adni-data/
- ADNI-DOD project and SCRNO guidance: https://adni.loni.usc.edu/support/experts-knowledge-base/question/?QID=920
- fMRIPrep: https://fmriprep.org/
- BIDS spec: https://bids.neuroimaging.io/
- OmniBrainBench: https://github.com/CUHK-AIM-Group/OmniBrainBench

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-08-11 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
