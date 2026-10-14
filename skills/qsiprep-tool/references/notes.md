# qsiprep-tool：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## What QSIPrep Typically Does (High-Level)

- Validates BIDS layout (or skips if requested)
- Creates brain mask(s)
- Denoising (optional), Gibbs unringing (optional)
- Susceptibility distortion correction (e.g., reverse phase-encoded b0 via topup-style approach)
- Eddy-current + motion correction (FSL eddy family behavior within containerized workflow)
- Gradient/bvec handling (rotation after motion correction)
- Coregistration to anatomical (and optionally standard space outputs)
- Produces derivatives + QC HTML reports

## Important Notes & Limitations

- **BIDS input is strongly recommended**. If you only have raw NIfTI/DICOM, use `bids-organizer` (and `dcm2nii`) first.
- QSIPrep benefits a lot from having **reverse phase-encoded b0 images (AP/PA)** or valid fieldmaps; otherwise distortion correction may be limited.
- Ensure adequate resources:
  - RAM commonly **16–64 GB**
  - Disk: work directory can be large (tens of GB)
  - `dipy-tool` for Python-based metrics/ROI features
  - MRtrix/FSL-based workflows (future tool skills) for tractography/connectomes

## Reference

- QSIPrep documentation and BIDS App usage (official docs; version-dependent)

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-04-05 02:01 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
