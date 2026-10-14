# mschallenge-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Origin**: ISBI 2015 Longitudinal MS Lesion Segmentation Challenge
- **Training**: 5 subjects, each with 2 timepoints (longitudinal)
- **Testing**: 14 subjects (hidden ground truth), 4-6 timepoints each
- **Modalities**: T1w, T2w, FLAIR, PD (co-registered)
- **Ground truth**: Manual lesion segmentation masks (training only)
- **Resolution**: ~0.5 × 0.5 × 0.5 mm (isotropic)
- **Preprocessing**: Skull-stripped, co-registered to common space
- **Reference**: Carass et al. (2017), NeuroImage

## Supported Modalities

| Modality | Description | Use in MS |
|---|---|---|
| T1w | T1-weighted structural | Brain atrophy, gray matter lesions |
| T2w | T2-weighted | White matter lesion detection |
| FLAIR | Fluid-Attenuated Inversion Recovery | Periventricular lesion detection |
| PD | Proton Density | Complementary lesion contrast |

## Important Notes and Limitations

- MS Challenge is a longitudinal dataset; consider timepoint effects in analysis.
- Ground truth masks are only available for training subjects.
- All images are preprocessed (skull-stripped, co-registered).
- Lesion segmentation is the primary task; standard brain morphometry may be affected by lesions.
- The challenge is designed for benchmarking; results should be compared with published baselines.
- `mschallenge-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- Carass et al. (2017): Longitudinal multiple sclerosis lesion segmentation: Resource and challenge. NeuroImage.
- ISBI 2015 MS Lesion Challenge: https://smart-stats-tools.org/lesion-challenge

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 13:31 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
