# dipy-tool：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes & Limitations

- **Preprocessing matters**: FA/MD are highly sensitive to motion/eddy/susceptibility distortions. Best practice is to run **topup/eddy** first (e.g., via `fsl-tool` or HCP diffusion pipeline) and use the **rotated bvecs** output by eddy.
- **DTI vs multi-shell**: DTI fitting is most stable on low b-values (commonly b≤1000–1200). Higher-order models (DKI/NODDI) require separate implementations (extend this tool if needed).
- **ROI alignment**: ROI/atlas labels must be in the *same voxel space* as the DWI-derived metrics. Registration/warping is handled by other tools (e.g., FSL/ANTs/HCP pipelines).
- **Numerical stability**: small negative eigenvalues can occur; this pipeline clips them to zero before FA computation.

## Reference

- DIPY documentation: https://dipy.org/documentation/latest/
- DIPY DTI reconstruction examples (tensor fitting + FA/MD/AD/RD)
- rs-fMRI-Pipeline-Tutorial: https://github.com/Karcen/rs-fMRI-Pipeline-Tutorial
- Curated code snippets in this skill:
    - `skills/dipy-tool/scripts/load_and_mask_reference.py`
    - `skills/dipy-tool/scripts/dti_metrics_reference.py`
    - `skills/dipy-tool/scripts/roi_stats_reference.py`

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-04-14 00:28 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
