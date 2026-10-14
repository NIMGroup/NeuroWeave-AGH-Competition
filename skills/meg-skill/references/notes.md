# meg-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## Supported MEG File Formats

| Format | System | Extension | Reader |
|---|---|---|---|
| Elekta/Neuromag | VectorView, TRIUX | `.fif` | `mne.io.read_raw_fif` |
| CTF | CTF MEG systems | `.ds` | `mne.io.read_raw_ctf` |
| KIT/Yokogawa | KIT, Ricoh | `.con`, `.mrk` | `mne.io.read_raw_kit` |
| BIDS MEG | Any (BIDS format) | `.meg.fif` | `mne.io.read_raw_fif` |

## Important Notes & Limitations

- MEG data is large (hundreds of MB to GB per recording); ensure sufficient disk space.
- Maxwell filtering (SSS) is specific to Elekta/Neuromag systems; CTF and KIT systems use different approaches.
- Source localization requires co-registered T1w MRI and MEG sensor positions (head position indicator coils or digitized head shape).
- MNE-Python is the primary backend; all MEG processing is built on MNE.
- MEG has millisecond temporal resolution but lower spatial resolution than fMRI.
- BIDS-MEG format follows the BIDS extension for MEG: https://bids-specification.readthedocs.io/en/stable/04-modality-specific-files/02-magnetoencephalography.html
- This skill is for research workflows; not for clinical decision-making.

## Reference

- MNE-Python: https://mne.tools/
- Gramfort et al. (2013): MEG and EEG data analysis with MNE-Python
- Taulu & Simola (2006): Spatiotemporal signal space separation (SSS)
- BIDS MEG: https://bids-specification.readthedocs.io/en/stable/04-modality-specific-files/02-magnetoencephalography.html
- Cam-CAN dataset: https://www.cam-can.org/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-05-06 12:19 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
