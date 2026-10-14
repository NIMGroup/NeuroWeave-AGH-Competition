# aibl-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
AIBL data is distributed through the **AIBL research portal**:
- Website: https://aibl.csiro.au/
- Data access: requires registration and data use agreement
- Imaging data available via LONI Image Data Archive (IDA): https://ida.loni.usc.edu/

### Supported AIBL Data Packages
- **Imaging data**: T1w MRI, PET (PiB amyloid, FDG metabolism, tau)
- **Phenotype data**: cognitive assessments (CDR, MMSE, MoCA, ADAS-Cog), blood biomarkers (Aβ42, Aβ40, p-tau, NfL), APOE genotype, lifestyle and demographic data
- **Derived imaging data**: FreeSurfer outputs (if available)

## Important Notes and Limitations

- AIBL imaging data includes both structural MRI and multiple PET tracers (PiB, FDG, tau). PET processing requires tracer-specific workflows.
- LONI IDA download requires authenticated access and compliance with the AIBL Data Use Agreement.
- AIBL subject IDs follow the format `XXX_S_XXXX`; normalization to BIDS labels must be consistent across all stages.
- AIBL has multiple follow-up timepoints (screening through 54-month); session handling must account for longitudinal structure.
- AIBL phenotype tables may use different delimiter formats across releases; auto-detection is recommended.
- PET SUVR computation requires reference region definition (e.g., cerebellar cortex for PiB, pons for FDG).
- `aibl-skill` is orchestration-only; detailed preprocessing logic remains in `smri-skill` and PET processing tools.

## Reference

- AIBL Study: https://aibl.csiro.au/
- LONI IDA: https://ida.loni.usc.edu/
- BIDS spec: https://bids.neuroimaging.io/
- BIDS PET extension: https://bids-specification.readthedocs.io/en/stable/04-modality-specific-files/09-positron-emission-tomography.html

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 12:19 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
