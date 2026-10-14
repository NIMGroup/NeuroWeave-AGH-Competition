# camcan-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: ~700 participants spanning the adult lifespan (18-88 years)
- **Design**: Cross-sectional population-based sample
- **Site**: MRC Cognition and Brain Sciences Unit, Cambridge, UK
- **Scanner**: Siemens 3T TIM TRIO
- **MEG system**: CTF 275-channel system
- **Access**: OpenNeuro ds003097 (CC0 license)

## Supported Modalities

| Modality | Description | Tasks/Conditions |
|---|---|---|
| T1w | High-resolution structural MRI | Single acquisition |
| T2*w | Functional MRI (multi-echo EPI) | Resting-state (eyes open), Movie-watching |
| MEG | Magnetoencephalography | Resting-state (eyes open), Auditory (passive listening), Visual (passive viewing) |
| dMRI | Diffusion-weighted imaging | DTI tractography |

### fMRI Tasks
| Task | Description | Duration |
|---|---|---|
| REST | Resting-state (eyes open) | ~8 min |
| MOVIE | Movie-watching (feature film excerpts) | ~15 min |

### MEG Tasks
| Task | Description | Duration |
|---|---|---|
| REST | Resting-state (eyes open, eyes closed) | ~8 min |
| AUDITORY | Passive listening to tones and speech | ~5 min |
| VISUAL | Passive viewing of visual stimuli | ~5 min |

## Important Notes and Limitations

- Cam-CAN is a population-based sample spanning the full adult lifespan (18-88 years).
- MEG data uses CTF 275-channel system; MEG processing requires system-specific handling.
- Movie-watching fMRI is a unique task paradigm; standard GLM may not apply.
- Age range is a key variable; consider age-stratified analyses.
- Data is available on OpenNeuro ds003097 in BIDS format.
- `camcan-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- Cam-CAN: https://camcan.mrc-cbu.cam.ac.uk/
- OpenNeuro ds003097: https://openneuro.org/datasets/ds003097
- Shafto et al. (2014): The Cam-CAN study protocol. BMC Neurology.
- Taylor et al. (2017): The Cam-CAN data repository. NeuroImage.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 13:31 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
