# bnt：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### 输入文件 (`data/braingnn_input/<atlas>/sub-<id>.pt`)
与 BrainGNN 共享同一数据源：

### BNT 内部表示
- 输入 tensor: `[B, N, N]` dense Pearson correlation matrix (tanh 反变换后)
- 若 include_t1: `[B, N, N+1]`（最后一列为 z-scored GM volume）
- 若 pos_encoding="identity": 模型内部拼接为 `[B, N, N+P]`

## 模型架构

默认 sizes: `[N//2, N//10]`（根据 ROI 数自动缩放）

## 调试经验与注意事项

1. **nhead 整除问题**：forward_dim = N + pos_embed_dim，必须能被 nhead 整除。模型已自动处理，但若手动指定 nhead 需注意。
2. **DEC encoder 维度**：DEC 内部 encoder 输入为 `input_feature_size * input_node_num`，ROI 数较大时参数量会爆炸。建议 encoder_hidden 保持 32。
3. **数据复用**：BNT 和 BrainGNN 共享 `data/braingnn_input/` 数据源，无需重复预处理。
4. **无 PyG 依赖**：BNT 完全基于标准 PyTorch，不需要 torch_geometric、torch_sparse 等。
5. **Attention 可解释性**：每层 TransPoolingEncoder 存储 attention weights，可用于 ROI 重要性分析。
6. **DEC assignment 可视化**：DEC pooling 的 soft assignment matrix 可映射回 ROI，展示聚类结构。
7. **collate 函数**：使用自定义 `bnt_collate` 返回 (fc_batch, y_batch, sid_list)，不要用默认 collate。
8. **regression 标签标准化**：与 BrainGNN 一致，对 y 做 z-score（基于训练集统计），评估时反变换。

## Reference

- Kan X, Dai W, Cui H, Zhang Z, Guo Y, He L. 2022. BrainNetworkTransformer. NeurIPS.
- Official repository: https://github.com/Wayfear/BrainNetworkTransformer

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-15 23:00 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
