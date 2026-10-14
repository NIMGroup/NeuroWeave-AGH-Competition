# brain-visualization：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes & Limitations

- This skill is for visualization and result packaging, not raw image preprocessing.
- Connectivity figures should usually threshold or sparsify edges; plotting full dense matrices produces unreadable figures.
- Atlas labels, atlas volume, and connectivity matrix dimensions must match.
- MNI-space summary workflows assume the volume has already been aligned or resampled appropriately.
- FreeSurfer colored PLY export depends on valid `.annot` files; missing or mismatched annotations will degrade vertex coloring.

## Reference

This skill is adapted from the visualization ideas and example workflows in:
- rs-fMRI-Pipeline-Tutorial: https://github.com/Karcen/rs-fMRI-Pipeline-Tutorial

Key referenced methods in the local tutorial copy:
- 3D brain network plotting with AAL atlas and Nilearn
- MNI152 zALFF regional activation ranking
- FreeSurfer colored cortical surface export to PLY

Curated reference snippets in this skill:
- `skills/brain-visualization/scripts/connectome_reference.py`
- `skills/brain-visualization/scripts/zalff_summary_reference.py`
- `skills/brain-visualization/scripts/freesurfer_ply_reference.py`

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-04-14 00:40 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
