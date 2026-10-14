# combraintf：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## 调试经验与注意事项

1. **nhead 整除 d_model**：d_model = n_roi，必须能被 nhead 整除。Schaefer_200/4/8 都 OK，AAL_116/4 也 OK，但奇数 ROI 数（如 cc200 实际 190）需调 nhead。
2. **每社区 CLS 独立**：每个社区一个 `local_transformer`（含独立 CLS 参数），但 Transformer 层本身可共享或独立。原始实现为完全独立（每社区 1 个 TransformerEncoderLayer），略多参数但更易调试。
3. **community 边界顺序**：模型按 community_ids 排序，每个 community 的 ROI 必须连续。`get_community_ids` 返回的 list 内部数字未必连续，模型构造时会重排索引。
4. **n_communities 推导**：默认从 `set(community_ids)` 大小取，无需显式传。
5. **memory 占用**：local transformer 数 × hidden_size × d_model^2 量级，glasser_360 + hidden=1024 容易爆显存，建议 hidden=512 + batch=4。
6. **DEC orthogonal init**：保持论文默认 `orthogonal=True, freeze_center=True, project_assignment=True`，否则训练不稳定。
7. **assignment 可视化**：DEC 输出 [B, N+1, n_clusters] soft assignment，可用于社区→功能子网络映射的可视化。

## Reference

- Bannadabhavi A, Lee S, Deng W, Ying R, Li X. 2023. Community-Aware Transformer for Autism Prediction in fMRI Connectome. MICCAI.
- Official repository: https://github.com/ubc-tea/Com-BrainTF

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-19 01:30 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
