# conn-tool：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes & Limitations

- CONN requires a valid MATLAB license and SPM installation.
- Best results are obtained when input data comes from `fmriprep-tool` or `hcppipeline-tool`.
- Long-running analyses (whole-brain seed-to-voxel, DCM model comparison) are automatically run in background mode.
- Execution begins **only after explicit user confirmation** of the full numbered plan.

## Reference

- Official CONN Toolbox Website: https://web.conn-toolbox.org/
- CONN Documentation: https://web.conn-toolbox.org/documentation

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-04-05 02:03 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
