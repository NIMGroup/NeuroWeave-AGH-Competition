# seed-iv-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Dataset Characteristics

- **Cohort**: 15 healthy subjects
- **Sessions**: 3 sessions per subject (different days)
- **Emotions**: 4 classes — happy, sad, fear, neutral
- **Trials**: 24 trials per session (6 per emotion)
- **Stimuli**: Short film clips designed to elicit specific emotions
- **EEG System**: ESI NeuroScan System, 62 channels
- **Sampling rate**: 1000 Hz (downsampled to 200 Hz commonly)
- **Reference**: Linked mastoids (M1/M2)
- **Access**: BCMI Lab (bcmi.sjtu.edu.cn/~seed/)
- **Format**: MATLAB .mat files (community BIDS conversion available)

## Supported Modalities

| Modality | Description | Details |
|---|---|---|
| EEG | 62-channel EEG | ESI NeuroScan, 1000 Hz |
| Eye tracking | Eye movement data | Gaze position, blinks |
| Physiological | GSR (galvanic skin response) | Skin conductance |

## SEED-IV Emotion Labels

| Label | Emotion | Trials per Session |
|---|---|---|
| 0 | Neutral | 6 |
| 1 | Sad | 6 |
| 2 | Fear | 6 |
| 3 | Happy | 6 |

## Important Notes and Limitations

- SEED-IV is a relatively small dataset (15 subjects); cross-subject generalization is challenging.
- 62-channel EEG provides rich spatial information for source localization.
- Differential Entropy (DE) features are the most commonly used for SEED-IV classification.
- Session-level normalization is recommended to handle inter-session variability.
- `seed-iv-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- SEED-IV: https://bcmi.sjtu.edu.cn/~seed/
- BCMI Lab, Shanghai Jiao Tong University
- Zheng & Lu (2015): Investigating Critical Frequency Bands and Channels for EEG-based Emotion Recognition with Deep Neural Networks. IEEE Trans. Autonomous Mental Development.

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 14:21 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
