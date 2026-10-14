# nsd-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: 8 healthy adults (subj01-subj08)
- **Scanner**: 7T Siemens MAGNETOM
- **Resolution**: 1.8mm isotropic voxels
- **Sessions**: ~30-40 scanning sessions per subject
- **Total fMRI**: ~30 hours per subject
- **Stimuli**: ~73,000 natural scene images from COCO dataset
- **Access**: OSF (Open Science Framework) and Amazon S3
- **Reference**: Allen et al. (2021), Nature Neuroscience

## Supported Modalities

| Modality | Description | Details |
|---|---|---|
| T1w | High-resolution structural MRI | 7T anatomical scans |
| task-fMRI | Visual task fMRI | Natural scene viewing with fixation task |
| dMRI | Diffusion-weighted imaging | White matter tractography |
| Retinotopy | Retinotopic mapping | Visual area identification |
| Eye-tracking | Gaze position data | During image viewing |

## NSD Task Paradigms

| Task | Description | Duration |
|---|---|---|
| NSD | Natural scene viewing (COCO images) | ~30-40 sessions × ~15 min each |
| FIXATION | Fixation task during image presentation | Continuous |

## Important Notes and Limitations

- NSD is a high-resolution 7T dataset; processing requires significant compute resources.
- 8 subjects with dense repeated measures (~30 hours of fMRI each).
- Visual neuroscience focus: standard task GLM may not apply; consider voxel-wise encoding models.
- COCO stimulus metadata is essential for stimulus-response analyses.
- Cortical surface-based representations using FreeSurfer outputs.
- `nsd-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- NSD: https://naturalscenesdataset.org/
- Allen et al. (2021): A massive 7T fMRI dataset to bridge cognitive neuroscience and artificial intelligence. Nature Neuroscience.
- COCO: https://cocodataset.org/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 13:31 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
