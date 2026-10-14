# rest-mneta-mdd-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: ~3,600+ participants
  - **MDD patients**: ~1,837 Major Depressive Disorder patients
  - **Healthy controls**: ~1,779 age/sex-matched controls
- **Sites**: 17 research sites across China
- **Scanner**: Multi-site (various 3T scanners)
- **Modalities**: rs-fMRI (primary), T1w sMRI (some sites)
- **Clinical**: Diagnosis (SCID), HAMD, HAMA, medication status
- **Access**: Chinese Data Sharing Platform, REST-meta-MDD consortium
- **Format**: NIfTI (community BIDS conversion available)
- **Reference**: Yan et al. (2019), Science Bulletin

## Supported Modalities

| Modality | Description | Details |
|---|---|---|
| rs-fMRI | Resting-state functional MRI | Eyes closed, 5-10 min |
| T1w | Structural MRI (some sites) | 1mm isotropic |

## REST-meta-MDD Clinical Measures

| Measure | Description | Domain |
|---|---|---|
| Diagnosis | MDD vs. Healthy Control (SCID-based) | Clinical status |
| HAMD | Hamilton Depression Rating Scale | Depression severity |
| HAMA | Hamilton Anxiety Rating Scale | Anxiety severity |
| Medication | Medication status (medicated vs. drug-naive) | Treatment |
| Site | Data collection site (1-17) | Multi-site |
| Age | Age at scan | Demographics |
| Sex | Biological sex | Demographics |
| Education | Years of education | Demographics |

## Important Notes and Limitations

- Multi-site data (17 sites) requires careful site effect handling.
- Scanner heterogeneity across sites introduces variability.
- rs-fMRI is the primary modality; structural data is limited.
- MDD diagnosis is SCID-based across all sites.
- Large sample size (~3,600) provides good statistical power for case-control analyses.
- Medication status is an important confound; subgroup analyses (medicated vs. drug-naive) are recommended.
- `rest-mneta-mdd-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- REST-meta-MDD: Chinese Data Sharing Platform
- Yan et al. (2019): Reduced default mode network functional connectivity in patients with recurrent major depressive disorder. Science Bulletin.
- REST-meta-MDD consortium

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 13:55 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
