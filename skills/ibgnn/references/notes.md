# ibgnn：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## 模型架构

每条边的消息：`msg(i,j) = Linear([h_i; h_j; edge_weight])`

## 调试经验与注意事项

1. **`edge_attr.abs()` 必须**：FC 矩阵带负相关，但 GCN 归一化和消息聚合假设非负权重，否则归一化得到 NaN。
2. **MPConv 参数量**：消息 MLP `Linear(2*hidden+1, hidden)`，每层约 `(2H+1)*H` 参数，比 GCN 重 ~2 倍。
3. **n_gnn_layers 选择**：论文 2-3 层最优；超过 3 层会过平滑。
4. **shortcut connection**：MLP head 内 `h = net(x) + shortcut(x)`，避免深网络梯度衰减。
5. **PyG 2.7 兼容**：使用 `torch_geometric.utils.scatter` 替代已废弃的 `torch_scatter.scatter_add`。
6. **数据复用**：与 BrainGNN 共享 `NeuroClawFCDataset`，无需重复加载/缓存。
7. **Explainer 未移植**：原 repo 的 `main_explainer.py`（GNNExplainer 后处理）暂未移植，若需可补充。

## Reference

- Cui H, Dai W, Zhu Y, Li X, He L, Yang C. 2022. Interpretable Graph Neural Networks for Connectome-Based Brain Disorder Analysis. MICCAI.
- Official repository: https://github.com/HennyJie/IBGNN
- BrainGB benchmark by same author: https://github.com/HennyJie/BrainGB

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-19 01:30 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
