# bold5000-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
BOLD5000 data is available on OpenNeuro:
- Website: https://bold5000-dataset.github.io/
- OpenNeuro: https://openneuro.org/datasets/ds002785

### Supported BOLD5000 Data Packages
- **Imaging data**: T1w structural, task-fMRI (NIfTI format)
- **Stimulus data**: 5,000 natural images with category labels and image metadata
- **Behavioral data**: Recognition memory judgments, response times
- **Participants**: 4 participants x ~1250 images each (high-density repeated measures)

## Important Notes and Limitations

- BOLD5000 is a small dataset (4 participants); statistical power is limited for group-level analyses.
- BOLD5000 uses high-density repeated image presentations; analysis requires handling of repeated measures.
- BOLD5000 data from OpenNeuro is already in BIDS format; re-staging may not be needed.
- Stimulus images are included in the dataset; event files reference image file names.
- `bold5000-skill` is orchestration-only; detailed preprocessing logic remains in `smri-skill` and `fmri-skill`.

## Reference

- BOLD5000: https://bold5000-dataset.github.io/
- Chang et al., 2019, *BOLD5000: A public fMRI dataset of 5,000 images*
- BIDS spec: https://bids.neuroimaging.io/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 01:52 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
