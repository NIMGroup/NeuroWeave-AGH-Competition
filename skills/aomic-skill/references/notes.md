# aomic-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
AOMIC data is publicly available:
- Website: https://nilab-uva.github.io/AOMIC.github.io/
- OpenNeuro derivatives: https://openneuro.org/
- Data access: direct download, no authentication required for most components

### Supported AOMIC Sub-datasets
- **AOMIC-ID1000**: ~1,000 participants with T1w, rs-fMRI, task-fMRI (emotion, gambling, motor, language tasks), Big Five personality, Raven's progressive matrices
- **AOMIC-PIOP1**: T1w, rs-fMRI, task-fMRI (emotion, working memory), personality and cognitive data
- **AOMIC-PIOP2**: T1w, rs-fMRI, task-fMRI (emotion, working memory), personality and cognitive data

## Important Notes and Limitations

- AOMIC data is already in BIDS format for many components; the reorganize script primarily validates and handles edge cases.
- AOMIC has multiple sub-datasets (ID1000, PIOP1, PIOP2) with slightly different task paradigms and phenotype measures.
- Task-fMRI event files (.tsv) must be preserved alongside BOLD data for proper task analysis.
- Some AOMIC components include physiological recordings (cardiac, respiration) that can be used for advanced denoising.
- `aomic-skill` is orchestration-only; detailed preprocessing logic remains in `smri-skill` and `fmri-skill`.

## Reference

- AOMIC: https://nilab-uva.github.io/AOMIC.github.io/
- OpenNeuro: https://openneuro.org/
- BIDS spec: https://bids.neuroimaging.io/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 11:24 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
