# scan-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Identifier and privacy rules

- Treat NACCID, PTID, visit dates, and crosswalks as controlled data even when images are defaced.
- Keep source identifiers out of public filenames, reports, model artifacts, and example data.
- Never attempt re-identification or facial reconstruction.
- Do not redistribute source images, subject-level tables, or restricted derivatives.
- Preserve the required SCAN/NACC acknowledgements and publication terms with the project provenance.

## References

- SCAN: https://scan.naccdata.org/
- NACC data access: https://www.naccdata.org/about-nacc-data
- Repository access matrix: `docs/DATASET_ACCESS.md`

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-08-11 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
