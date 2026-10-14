# fm_app：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Limitations and Notes

- CUDA-capable GPU is strongly recommended for training.
- Stage 2 depends on valid Stage 1 artifacts and sMRI features.
- Stage 3 depends on `stage1_dataset_*.pt` and proper masking setup.
- Keep train/val/test split strict to avoid leakage.
- Verify phenotype column counts and subject ID alignment before Stage 1.

## Reference

- He Z, Li W, Liu Y, et al. FM-APP: Foundation model for any phenotype prediction via fMRI to sMRI knowledge transfer. IEEE Transactions on Medical Imaging, 2024, 44(10): 4010-4022.
- Official repository: https://github.com/ZhibinHe/FM-APP
- NeuroSTORM HCP download scripts: https://github.com/CUHK-AIM-Group/NeuroSTORM/tree/main/scripts/dataset_download

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-03-28 20:03 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
