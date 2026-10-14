# hcppipeline-tool：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes & Limitations

- HCP Pipelines are very resource-intensive and disk-heavy.
- Requires well-organized input data (preferably BIDS via `bids-organizer` first).
- For benchmark or contract-sensitive tasks, do not stop at the bare `PreFreeSurferPipeline.sh --path --subject --t1 --t2` surface. First resolve BIDS subject/session inputs, validate required template/config resources, and make missing-input or no-fieldmap/no-gdc decisions explicit.
- ICA-FIX denoising is one of the strongest features of HCP functional pipeline.
- Surface-based MSMAll registration provides superior alignment compared to volume-based methods.

## Reference

Official HCP Pipelines integration for NeuroClaw high-precision multimodal preprocessing.  
Official repository: https://github.com/Washington-University/HCPpipelines

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-04-05 02:03 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
