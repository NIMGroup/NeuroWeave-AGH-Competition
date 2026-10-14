# pet-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Tracer-Specific Reference Regions

| Tracer | Target | Reference Region | SUVR Threshold (amyloid+) |
|---|---|---|---|
| **PiB** (¹¹C-Pittsburgh Compound B) | Amyloid-β deposition | Cerebellar cortex (gray matter) | SUVR > 1.42 or > 1.21 (centiloid-adjusted) |
| **FDG** (¹⁸F-Fluorodeoxyglucose) | Glucose metabolism (hypometabolism pattern) | Pons or whole cerebellum | Lower SUVR = worse metabolism |
| **Tau** (¹⁸F-Flortaucipir / AV-1451) | Tau neurofibrillary tangles | Cerebellar cortex (gray matter) | SUVR > 1.2–1.3 (region-dependent) |

## Important Notes & Limitations

- PET images are typically low-resolution (~2–4 mm); coregistration to high-resolution T1w is essential.
- Reference region selection is tracer-dependent; using the wrong reference region invalidates SUVR.
- Partial volume correction is recommended for atrophy-prone populations (e.g., Alzheimer's disease).
- Dynamic PET requires frame timing information from DICOM headers or sidecar JSON.
- Static PET (single late frame) is sufficient for most clinical SUVR analyses.
- This skill is for research workflows; not for clinical decision-making.

## Reference

- Klunk et al. (2004): PiB amyloid imaging
- Landau et al. (2012): Amyloid imaging with PiB and florbetapir
- Baker et al. (2017): AV-1451 tau PET imaging
- BIDS PET extension: https://bids-specification.readthedocs.io/en/stable/04-modality-specific-files/09-positron-emission-tomography.html
- FSL: https://fsl.fmrib.ox.ac.uk/fsl/
- FreeSurfer: https://surfer.nmr.mgh.harvard.edu/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-05-06 12:19 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
