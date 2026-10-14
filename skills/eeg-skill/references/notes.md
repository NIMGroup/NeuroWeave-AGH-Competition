# eeg-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Important Notes & Limitations

- Waveform-to-spectrogram conversion is handled by `torchaudio.transforms.MelSpectrogram`.
- Frequency-band energy extraction is performed via continuous wavelet transform (`scipy.signal.cwt` + `morlet2` wavelet).
- Execution begins **only after explicit user confirmation** of the full numbered plan.
- All outputs are saved in `./eeg_output/` with clear subfolders (raw/, filtered/, epoched/, features/, spectrograms/, etc.).

## Reference

Core libraries: MNE-Python (main), `torchaudio.transforms.MelSpectrogram` (waveform to spectrogram), `scipy.signal.cwt` + `morlet2` (frequency band energy extraction).

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-04-05 02:01 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
