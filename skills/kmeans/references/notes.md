# kmeans：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## When to Use K-means

- The user wants data-driven brain region partitioning rather than using a predefined atlas.
- The goal is to derive parcel labels for downstream connectivity, decoding, or visualization.
- A classical unsupervised clustering baseline is preferred over deep learning.
- The user wants fixed parcel count with simple optimization.

## Limitations and Notes

- Clustering quality depends strongly on preprocessing, feature definition, and spatial normalization.
- K-means is sensitive to initialization and requires a fixed cluster count.
- Data-driven parcellations may vary across cohorts and may not align directly with standard atlases.

## Reference

- Thirion B, Varoquaux G, Dohmatob E, Poline JB. Which fMRI clustering gives good brain parcellations?
- Nilearn regions and parcellations documentation: https://nilearn.github.io/stable/connectivity/region_extraction.html

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-04-14 00:45 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
