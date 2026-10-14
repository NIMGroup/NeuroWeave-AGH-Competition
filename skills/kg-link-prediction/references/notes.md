# kg-link-prediction：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`kg-link-prediction` trains embeddings or graph neural encoders on NeuroOracle
triples and scores candidate relations.

**Supported models**

| Model | Encoder | Decoder/evaluation |
|---|---|---|
| `complex` | complex-valued entity/relation embeddings | ComplEx score |
| `rgcn` | relation-specific graph convolution | relation-aware DistMult |
| `graphsage` | neighborhood aggregation | relation-aware DistMult |
| `gat` | graph attention | relation-aware DistMult |

GNN message passing uses training edges only. Validation and test edges are
excluded from encoder adjacency, and negative samples are checked against all
known positive triples.

### 1. Prepare a NeuroOracle graph

Input is a NeuroOracle `knowledge_graph.json` containing typed nodes and
confidence-bearing edges. Low-confidence edges can be excluded with
`--min-confidence`.

### 3. Train GraphSAGE or GAT

Change `--model` to `graphsage` or `gat`. Keep the same split seed when
comparing encoders.

### 4. Train the existing ComplEx route

Retrain the model whenever the graph snapshot changes materially. Record the
graph hash and freeze year when the embeddings are used for hindcasting.

## Reference

- Trouillon et al. ComplEx (2016).
- Schlichtkrull et al. R-GCN (2018).
- Hamilton et al. GraphSAGE (2017).
- Velickovic et al. Graph Attention Networks (2018).

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
