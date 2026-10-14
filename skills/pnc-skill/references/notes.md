# pnc-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: ~9,000+ youth aged 8-21 years
- **Scanner**: 3T Siemens TIM Trio
- **Modalities**: T1w sMRI, rs-fMRI, task-fMRI, dMRI/DTI
- **Task paradigms**: Go/No-Go, Fraternal Twins, Penn Line Orientation, Penn Word Memory
- **Clinical**: Psychiatric assessment, cognitive battery (Penn CNB)
- **Access**: NIMH Data Archive (NDA), OpenNeuro ds000030 (BIDS subset)
- **Format**: BIDS-compliant (community conversion)
- **Reference**: Satterthwaite et al. (2014), NeuroImage

## Supported Modalities

| Modality | Description | Tasks/Conditions |
|---|---|---|
| T1w | High-resolution structural MRI | 1mm isotropic |
| rs-fMRI | Resting-state functional MRI | Eyes open |
| task-fMRI | Task-based functional MRI | Go/No-Go, Emotion, Line Orientation, Word Memory |
| dMRI | Diffusion-weighted imaging | DTI, white matter tractography |

## PNC Task Paradigms

| Task | Description | Cognitive Domain |
|---|---|---|
| Go/No-Go | Response inhibition / impulse control | Executive function |
| Fraternal Twins | Emotion recognition | Social cognition |
| Penn Line Orientation | Spatial processing | Visuospatial |
| Penn Word Memory | Memory encoding/retrieval | Episodic memory |

## Important Notes and Limitations

- PNC is a developmental cohort; analyses should account for age effects (8-21 years).
- Pediatric data may require adjusted preprocessing parameters (e.g., higher motion thresholds).
- Penn CNB (Computerized Neurocognitive Battery) provides rich cognitive phenotyping.
- Psychiatric assessment includes DSM-based diagnoses.
- Large sample size enables well-powered developmental analyses.
- `pnc-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- PNC: https://www.med.upenn.edu/bbl/
- Satterthwaite et al. (2014): Neuroimaging of the Philadelphia Neurodevelopmental Cohort. NeuroImage.
- OpenNeuro ds000030
- NIMH Data Archive: https://nda.nih.gov/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 13:55 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
