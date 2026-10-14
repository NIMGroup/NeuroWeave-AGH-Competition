# freesurfer-tool：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes & Limitations

- Input must be NIfTI (`.nii` or `.nii.gz`); convert DICOM first using `dcm2nii` skill.  
- Output follows standard FreeSurfer structure: `$SUBJECTS_DIR/<subjid>`.  
- Windows users: strongly recommended to use WSL2 (installation handled by `dependency-planner`).  
- License check: skill assumes `license.txt` is already in place after `dependency-planner` run.

## Reference

Official site: https://surfer.nmr.mgh.harvard.edu  
Latest stable release (as of 2026): FreeSurfer 8.1.0 or newer  
License registration: https://surfer.nmr.mgh.harvard.edu/registration.html  
Documentation: https://surfer.nmr.mgh.harvard.edu/fswiki/FreeSurferWiki  

Curated reference snippet in this skill:
- `skills/freesurfer-tool/scripts/freesurfer_processor.py`

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-04-05 02:03 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
