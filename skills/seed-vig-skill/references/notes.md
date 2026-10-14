# seed-vig-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: 23 healthy subjects
- **Task**: Simulated driving task (vigilance decrement paradigm)
- **EEG System**: 17-channel EEG (ESI NeuroScan or dry electrodes)
- **Sampling rate**: 200 Hz
- **Reference**: Linked mastoids (M1/M2)
- **Labels**: Vigilance levels (KSS scale or EEG-derived)
- **Duration**: ~2 hours per subject
- **Access**: BCMI Lab (bcmi.sjtu.edu.cn/~seed/)
- **Format**: MATLAB .mat files (community BIDS conversion available)

## Supported Modalities

| Modality | Description | Details |
|---|---|---|
| EEG | 17-channel EEG | ESI NeuroScan, 200 Hz |
| Eye tracking | Eye movement data | Blinks, gaze position |
| Peripheral | EOG, EMG | Eye/muscle artifacts |

## SEED-VIG Vigilance Labels

| Label | Description | Method |
|---|---|---|
| KSS | Karolinska Sleepiness Scale | Self-report (1-9) |
| EEG-based | Theta/alpha/beta power ratios | Spectral analysis |
| Binary | Alert vs. Drowsy | Threshold-based |

## Important Notes and Limitations

- 17-channel EEG provides limited spatial resolution compared to high-density systems.
- Simulated driving may not fully replicate real-world drowsiness.
- Theta/alpha/beta power ratios are commonly used spectral features for vigilance detection.
- Cross-subject calibration is often needed due to individual differences in EEG patterns.
- `seed-vig-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- SEED-VIG: https://bcmi.sjtu.edu.cn/~seed/
- BCMI Lab, Shanghai Jiao Tong University
- Wei et al. (2017): EEG-based vigilance estimation using extreme learning machines. Neurocomputing.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 14:21 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
