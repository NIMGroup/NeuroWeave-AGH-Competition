# dictlearning：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## When to Use DictLearning

- The user wants resting-state network decomposition rather than task activation analysis.
- The goal is to identify sparse intrinsic connectivity networks from rs-fMRI.
- The user wants subject-level component time series for downstream connectivity or clustering.
- Sparse and interpretable network components are preferred.
- A lightweight classical unsupervised method is preferred over deep learning.

## Limitations and Notes

- Results are sensitive to preprocessing quality, head motion, filtering, and masking choices.
- The number of components strongly influences decomposition granularity.
- DictLearning is unsupervised and does not directly provide statistical group inference.
- Downstream comparisons across groups usually require additional statistical analysis after decomposition.

## Reference

- Varoquaux G et al. Dictionary learning for resting-state fMRI atlas extraction.
- Nilearn decomposition documentation: https://nilearn.github.io/stable/connectivity/resting_state_networks.html

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-04-14 00:45 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
