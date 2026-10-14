# abide-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
ABIDE data is distributed through the **FCP/INDI** repository:
- ABIDE I: https://fcon_1000.projects.nitrc.org/indi/abide/
- ABIDE II: https://fcon_1000.projects.nitrc.org/indi/abide_II.html
- NITRC mirror: https://www.nitrc.org/projects/fcp_indi/

### Supported ABIDE Data Packages
- **ABIDE I**: 1,112 subjects from 17 international sites (539 ASD, 573 controls)
- **ABIDE II**: 1,044 subjects from 19 sites
- **Phenotype data**: CSV files with demographics, diagnosis, cognitive scores
- **Preprocessed derivatives** (optional): CPAC, DPARSF, CCS, NeuroMark pipelines

## Important Notes and Limitations

- ABIDE data from different sites may have varying acquisition parameters; site effects should be accounted for in analysis.
- ABIDE subject IDs are numeric and vary in length across sites.
- ABIDE I and II have different phenotype table formats; the extraction script handles both.
- ABIDE provides preprocessed derivatives from multiple pipelines (CPAC, DPARSF, CCS); raw data processing via fMRIPrep is recommended for reproducibility.
- ABIDE data does not include task-fMRI; only resting-state fMRI is available.
- `abide-skill` is orchestration-only; detailed preprocessing logic remains in `smri-skill` and `fmri-skill`.

## Reference

- ABIDE I: https://fcon_1000.projects.nitrc.org/indi/abide/
- ABIDE II: https://fcon_1000.projects.nitrc.org/indi/abide_II.html
- Di Martino et al., 2014, *The autism brain imaging data exchange: towards a large-scale evaluation of the intrinsic brain architecture in autism*
- BIDS spec: https://bids.neuroimaging.io/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 01:45 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
