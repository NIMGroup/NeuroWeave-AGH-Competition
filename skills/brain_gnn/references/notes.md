# brain_gnn：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## 调试经验与注意事项

1. **indim/nroi 自动推断**：train.py 从第一个样本自动获取 `n_roi` 和 `indim`，无需手动指定。
2. **PyG cache 问题**：修改数据后需删除 `data/braingnn_cache/<atlas>/` 目录，否则会加载旧缓存。sweep_atlases.py 已自动处理。
3. **edge_attr 维度**：augment_adj 后 edge_attr 可能变为 1D，pool 层需要 squeeze 处理（已在 forward 中处理）。
4. **小样本 kfold 自动调整**：当最小类别样本数 < kfold 时，自动降低 kfold 避免 StratifiedKFold 报错。
5. **regression 标签标准化**：默认对 y 做 z-score（基于训练集统计），评估时反变换回原始尺度。
6. **softmax 在 message 中**：MyNNConv 对 edge_weight 做 softmax attention，因此输入 edge_attr 必须为非负值（|Pearson r|）。
7. **ROI mask 支持**：data_adapter 支持 `roi_mask` 参数做子图选择，用于消融实验。

## Reference

- Li X, Zhou Y, Dvornek N, Zhang M, Gao S, Zhuang J, Scheinost D, Staib L, Ventola P, Duncan J. 2020. BrainGNN.
- Official repository: https://github.com/xxlya/BrainGNN_Pytorch/tree/main
- PyG installation notes: https://pytorch-geometric.readthedocs.io/en/latest/notes/installation.html

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-15 22:50 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
