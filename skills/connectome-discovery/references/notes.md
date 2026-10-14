# connectome-discovery：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`connectome-discovery` is a scientific interpretation workflow, not a second
CPM implementation. It consumes fitted-model outputs or aligned network maps
and produces map similarities, empirical significance, and candidate target
rankings.

| Stage | Canonical owner |
|---|---|
| Connectome prediction | `cpm`, BrainGNN, BNT, or another model skill |
| Atlas/space validation | `fmri-skill`, `nibabel-skill` |
| Map similarity and permutation | `models/connectome_discovery/mapping.py` |
| Surface/network rendering | `brain-visualization` |

### 1. Generate model evidence

Run `cpm` or another connectome model and freeze its held-out predictions,
selected edges, atlas, and node ordering.

### 2. Align maps

Reference and candidate maps must use the same atlas, node order, hemisphere
convention, and value orientation. Resampling or atlas mapping must be recorded.

### 3. Score and rank targets

Use `models/connectome_discovery/mapping.py` for:

- `cosine_similarity_map`
- `permutation_pvalue`
- `rank_targets`

Save the observed score, null distribution settings, permutation count, random
seed, atlas, and coordinate space.

### 4. Visualize

Route final ROI/network values to `brain-visualization`. Do not infer an
anatomical target from an unlabeled edge vector.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
