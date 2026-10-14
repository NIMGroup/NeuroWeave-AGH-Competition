# nibabel-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes & Limitations

- `nibabel-skill` is not a replacement for preprocessing tools such as FSL, fMRIPrep, or Nilearn workflows.
- Affine correctness matters: voxel coordinates are meaningless without the right affine transform.
- Atlas label files and atlas volumes may not align perfectly by naming convention; always validate label counts.
- FreeSurfer `.annot` label ids are not always a direct 0..N index into user expectations; inspect the returned tables carefully.

## Reference

This skill is adapted from the nibabel-related code patterns in:
- rs-fMRI-Pipeline-Tutorial: https://github.com/Karcen/rs-fMRI-Pipeline-Tutorial

Curated reference snippets in this skill:
- `skills/nibabel-skill/scripts/nifti_inspection_reference.py`
- `skills/nibabel-skill/scripts/atlas_coordinate_reference.py`
- `skills/nibabel-skill/scripts/freesurfer_io_reference.py`

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-04-14 00:23 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
