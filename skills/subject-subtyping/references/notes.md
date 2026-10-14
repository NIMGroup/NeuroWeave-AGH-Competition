# subject-subtyping：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`subject-subtyping` discovers unsupervised subject groups from tabular imaging or
multimodal features. It exports subtype assignments, latent embeddings,
silhouette diagnostics, and a reusable checkpoint.

**Supported models**

| Model | Method | Typical use |
|---|---|---|
| `kmeans` | Euclidean partitioning | compact baseline |
| `gmm` | Gaussian mixture | soft distributional subtypes |
| `spectral` | graph spectral clustering | non-convex structure |
| `nmf` | non-negative embedding + K-means | parts-based phenotypes |
| `consensus` | bootstrap co-clustering | stability-focused analysis |
| `pca` | PCA embedding + K-means | linear latent subtypes |
| `autoencoder` | neural embedding + K-means | nonlinear latent subtypes |

Outcome labels must not be used to choose the number of clusters. Clinical
outcomes may be tested only after subtype definitions are frozen.

### 1. Prepare features

Input is a CSV with `subject_id` and numeric features. Do not include diagnosis,
survival, or treatment outcome columns among the clustering features.

```text
subject_id,roi_001,roi_002,network_fc,brain_age_gap
sub-001,0.12,-0.04,0.31,2.1
sub-002,0.08,-0.09,0.27,-1.4
```

### 4. Select the number of clusters

Run a prespecified range such as `k=2..8`, compare silhouette and bootstrap
stability, then freeze `k` before association with clinical endpoints. Repeat
the final model across seeds when cluster stability is central to the claim.

## Reference

- Consensus clustering uses bootstrap co-assignment frequencies.
- NMF inputs are transformed to a non-negative scale before decomposition.
- PCA and autoencoder modes cluster the learned embedding rather than raw data.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-07-29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
