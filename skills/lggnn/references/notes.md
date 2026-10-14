# lggnn：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## 调试经验与注意事项

1. **MI loss 符号**：原论文 `loss = loss_cla - 0.1 * mi_loss`（最大化 MI），原始实现保持相同符号约定。
2. **SABP 输出形状**：池化后节点数变为 `floor(ratio * N)`，注意 batch 后的 graph_emb 是 `[B, embed_dim]`。
3. **ratio 调参**：论文 ratio=0.9（仅丢 10% ROI）需要 hgc=16；原始实现用 hgc=64 时 ratio=0.5 更稳定。
4. **PyG 2.7 兼容**：旧版 `topk_pool.topk/filter_adj` 已移除，使用 `pool.select.topk.topk` 与内联 `_filter_adj`。
5. **MI 初始为 0**：训练开始时 mi_estimate ≈ 0（因 joint 和 margin 分布相近），训练过程中应逐步变正。
6. **数据复用**：与 BrainGNN 共享 `NeuroClawFCDataset`，无需重复加载/缓存。

## Reference

- Zhang Y, Zhan L, Cai W, Thompson P, Huang H. 2022. Local to Global Hierarchical Graph Neural Network for Brain Disorder Diagnosis. MICCAI.
- Official repository: https://github.com/cnuzh/LG-GNN

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-19 01:30 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
