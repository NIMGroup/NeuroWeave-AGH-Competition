# neurostorm：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Overview

`neurostorm-skill` wraps the unified **NeuroSTORM fMRI platform** (CUHK-AIM-Group), which, as of the 2026-05-08 release, ships **8 model implementations** under a single training/fine-tuning entry point. Use this skill for the full lifecycle: data download, preprocessing, pretraining, fine-tuning, and inference.

**Supported models (8)**

| Model | Input type | Graph? | Backbone |
|-------|-----------|--------|----------|
| `neurostorm` | voxel (4D) | No | Mamba-SSM |
| `swift` | voxel (4D) | No | Swin 4D Transformer |
| `braingnn` | FC graph (2D) | Yes | GNN |
| `bnt` | FC matrix (2D) | No | Transformer |
| `lggnn` | ROI + FC | Yes | Learnable GNN |
| `combraintf` | FC matrix (2D) | No | Community-aware Transformer |
| `ibgnn` | FC graph (2D) | Yes | Interpretable GNN |
| `brainnetcnn` | FC matrix (2D) | No | CNN |

**Supported tasks**

| ID | Task |
|----|------|
| 1 | Age & Gender Prediction |
| 2 | Phenotype Prediction |
| 3 | Disease Diagnosis |
| 4 | fMRI Retrieval |
| 5 | Task fMRI State Classification |

**Supported datasets:** HCP1200, ABCD, UKB, Cobre, ADHD200, HCPA, HCPD, UCLA, HCPEP, HCPTASK, GOD, NSD, BOLD5000.

**Dual data formats:** `PT` (faster random access, larger disk) and `H5` (compact, scales to large cohorts). Choose at preprocessing and at training via `--output_format` / `--data_format`.

### 1. Data Preprocessing

Assume raw fMRI is in MNI152 space (apply FSL / fMRIPrep / HCP pipelines first).

Auxiliary scripts: `datasets/compute_stats_and_mask.py`, `datasets/compute_atlas_map.py`.

### 2. Pretraining

NeuroSTORM supports two pretraining strategies via `main.py`.

**MAE pretraining (NeuroSTORM):**

**Contrastive pretraining (SwiFT-style):**

Ready-made scripts in `scripts/hcp_pretrain/`.

### 3. Fine-tuning

The same `main.py` handles every model; switch with `--model` and (for graph/FC models) `--data_type` / `--atlas_name` / `--fc_type` / `--num_rois`.

**NeuroSTORM — gender classification:**

**NeuroSTORM — age regression (with label standardization):**

**BrainGNN (FC graph input):**

**BrainNetworkTransformer (BNT, hierarchical pooling):**

**BrainNetCNN:**

**LG-GNN, Com-BrainTF, IBGNN**: same pattern — set `--model` and choose the matching `--data_type` (`fc_graph` for GNNs, `fc_bnt` for transformer/CNN FC inputs). See `scripts/run_braingnn.sh`, `scripts/run_bnt.sh`, and other `scripts/*_downstream/` folders for templates.

**Useful fine-tuning flags**

| Flag | Purpose |
|------|---------|
| `--data_format {auto,pt,h5}` | select preprocessed file format |
| `--load_model_path` | load pretrained backbone weights |
| `--freeze_feature_extractor` | freeze backbone, train head only |
| `--resume_ckpt_path` | resume from Lightning checkpoint |
| `--use_scheduler --milestones 50 100` | multi-step LR |
| `--optimizer AdamW --weight_decay 0.01` | switch optimizer |
| `--augment_during_training` + `--augment_only_affine` / `--augment_only_intensity` | data augmentation |
| `--gpu_ids 0,1,2` / `--num_gpus 4` | GPU selection (DDP auto when >1) |
| `--loggername tensorboard --project_name NAME` | logging |

### 4. Inference / Demo

**Single subject:**

Task options include `age`, `gender`, `phenotype` (with `--phenotype_name` + `--phenotype_type`).

**Batch inference on a test split:**

Or run the bundled script: `sh scripts/run_demo.sh`.

## Reference

- Paper: *Towards a General-Purpose Foundation Model for fMRI Analysis*, Wang et al., Nature Biomedical Engineering, 2026. https://www.nature.com/articles/s41551-026-01666-y
- Project: https://cuhk-aim-group.github.io/NeuroSTORM/
- GitHub: https://github.com/CUHK-AIM-Group/NeuroSTORM
- Upstream docs: `INSTALLATION.md`, `USER_GUIDE.md`

Model attributions: SwiFT (Transconnectome), BrainGNN (LifangHe), BNT (Wayfear), LG-GNN (cnuzh), Com-BrainTF (ubc-tea), IBGNN (HennyJie), BrainNetCNN (nicofarr).

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT。
原说明更新时间：2026-05-11 20:45 HKT  (synced to upstream 2026-05-08 release: +7 models, dual PT/H5, FC pipeline, pytest suite)。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
