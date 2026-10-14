# dcm2nii：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes & Limitations

- Excellent support for MRI (GE, Siemens, Philips, Hitachi), CT, PET, XA
- 4D data (fMRI, DWI, ASL, perfusion) well handled
- Philips enhanced DICOM & private tags → very good parsing
- **Does NOT** convert RTSTRUCT / RTDOSE / SEG (use other tools)
- Compression requires pigz (faster) or internal deflate
- JSON sidecar contains most clinically relevant tags (BIDS-ish)
- Always **verify** output orientation & voxel size in viewer (FSLeyes, ITK-Snap, FreeSurfer)
- For very large studies → consider `-m y` + parallel runs

## Reference

Original & latest: https://github.com/rordenlab/dcm2niix  
Documentation: https://www.nitrc.org/plugins/mwiki/index.php/dcm2nii:MainPage  
Maintainer: Chris Rorden  
Core algorithm: dicom → NIfTI reorientation + private tag parsing

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：BSD 3-Clause (original dcm2niix license). See https://github.com/rordenlab/dcm2niix/blob/master/LICENSE for complete terms.。
原说明更新时间：2026-03-25 20:53 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
