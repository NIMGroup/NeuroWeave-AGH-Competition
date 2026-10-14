# nii2dcm：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes & Limitations

- Supports only **3D single-volume → multi-slice 2D DICOM series** (common in structural MRI, segmentations)
- **No 4D support** (fMRI, DWI, perfusion, DTI) — split volumes first if needed
- Without `--dicom-type`, output is generic (lacks modality-specific tags)
- Without `--ref-dicom`, patient/study info is anonymized/generic
- Orientation, spacing, slice thickness read from NIfTI; verify alignment
- **Always visually validate** output in DICOM viewer (Horos, 3D Slicer, ITK-Snap, MITK)
- Project still in early stage — expect occasional bugs
- **Research purpose only** — not a clinical tool

## Reference

Original: https://github.com/tomaroberts/nii2dcm (v0.1.6, May 2025)  
Built on: highdicom (DICOM creation), SimpleITK (image I/O)  
Inspired by: dcm2niix (reverse tool), SVRTK project

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：BSD 3-Clause (original nii2dcm license). See https://github.com/tomaroberts/nii2dcm/blob/main/LICENSE for complete terms.。
原说明更新时间：2026-03-26 00:21 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
