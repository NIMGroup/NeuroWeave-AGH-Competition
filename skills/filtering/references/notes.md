# filtering：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## When to Use Filtering

- The user wants signal cleaning rather than statistical modeling or prediction.
- The goal is to remove unwanted frequency content before connectivity or decoding.
- The workflow needs standardized temporal preprocessing before ROI extraction.
- A classical transparent denoising baseline is preferred over learned denoising methods.
- The user explicitly asks for band-pass filtering, high-pass filtering, or low-pass filtering.

## Limitations and Notes

- Filtering choices depend strongly on TR, study design, and whether the data are resting-state or task-fMRI.
- Over-aggressive filtering can remove meaningful task-related or physiological signals.
- Temporal cleaning parameters should be reported because they directly affect downstream analyses.

## Reference

- Lindquist MA. The statistical analysis of fMRI data.
- Nilearn signal cleaning documentation: https://nilearn.github.io/stable/modules/generated/nilearn.image.clean_img.html

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-04-14 00:45 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
