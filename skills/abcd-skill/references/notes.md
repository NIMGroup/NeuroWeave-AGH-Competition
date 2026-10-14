# abcd-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
Current ABCD data is distributed through the **NIH Brain Development Cohorts (NBDC) Data Sharing Platform**:
- Access instructions: https://docs.abcdstudy.org/latest/usage/access.html
- Data access process: https://www.nbdc-datahub.org/data-access-process
- Download applications: Data Exploration and Analysis Portal (DEAP) or the NBDC Data Access Platform
- Access requires an approved NBDC Data Use Certification (DUC), an NIH-recognized institutional affiliation, institutional signing-official approval, and an active Federalwide Assurance (FWA).

Beginning with ABCD release 6.0, current releases are no longer held at NDA. Use NDA only when the task explicitly targets a legacy release that remains there.

### Supported ABCD Data Packages
- **ABCD Study 7.0** (current release as of 2026-08-25): includes imaging and non-imaging data documented by NBDC
- **Imaging data**: T1w, T2w, dMRI, rs-fMRI, task-fMRI (NIfTI format)
- **Phenotype data**: tab-delimited files (abcd_p_tab, mental_health, cbcl, etc.)
- **Derived imaging data**: release-specific derived packages when available through NBDC

## Important Notes and Limitations

- ABCD multimodal processing is resource intensive (CPU, RAM, and storage). Full cohort imaging data exceeds 10 TB.
- NBDC download requires authenticated access and an approved institutional DUC.
- ABCD subject IDs use NDAR format; normalization to BIDS labels must be consistent across all stages.
- ABCD has multiple longitudinal follow-up events; derive session handling from the selected release instead of hard-coding a maximum follow-up year.
- ABCD phenotype tables use tab-delimited format with specific column naming conventions; column names may change across releases.
- `abcd-skill` is orchestration-only; detailed preprocessing logic remains in `smri-skill`, `fmri-skill`, and `dwi-skill`.

## Reference

- ABCD Study: https://abcdstudy.org/
- ABCD data access: https://docs.abcdstudy.org/latest/usage/access.html
- ABCD 7.0 release notes: https://docs.abcdstudy.org/latest/documentation/release_notes/7_0.html
- NBDC repository transition notice: https://grants.nih.gov/grants/guide/notice-files/NOT-DA-24-038.html
- ABCD BIDS App: https://github.com/ABCD-STUDY/abcd-bids-tfmri
- BIDS spec: https://bids.neuroimaging.io/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-08-25 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
