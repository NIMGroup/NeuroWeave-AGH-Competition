# asl-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## ASL Labeling Strategies

| Strategy | Description | Typical Parameters |
|---|---|---|
| **pCASL** (pseudo-Continuous ASL) | Most common; single PLD, good SNR | Label duration: 1.5–2.0 s, PLD: 1.5–2.0 s |
| **CASL** (Continuous ASL) | Longer labeling, higher SNR but more sensitive to transit effects | Label duration: 2–4 s, PLD: 1–2 s |
| **PASL** (Pulsed ASL) | Short labeling, lower SNR, no separate M0 needed (QUIPSS II) | Bolus thickness: 10–15 cm, TI1/TI2: 700/1800 ms |

## Core CBF Quantification Model

The Buxton single-compartment model for pCASL:

```
CBF = (6000 * ΔM * λ) / (2 * α * M0 * T1b * (exp(-w/T1b) - exp(-(τ+w)/T1b)))   [mL/100g/min]
```

Where:
- ΔM = ASL difference image (control - label)
- M0 = equilibrium magnetization of arterial blood
- λ = blood-tissue water partition coefficient (0.9 mL/g)
- α = labeling efficiency (0.85 for pCASL, 0.95 for CASL, 0.98 for PASL)
- T1b = T1 of arterial blood at 3T (~1.65 s) or 1.5T (~1.35 s)
- w = post-labeling delay (PLD)
- τ = label duration

## Important Notes & Limitations

- ASL has inherently low SNR compared to BOLD fMRI; averaging multiple control-label pairs is recommended.
- M0 image is required for absolute CBF quantification; if absent, only relative CBF can be computed.
- PLD and labeling duration must be known from the acquisition protocol; incorrect values invalidate CBF.
- At 3T, T1b ≈ 1.65 s; at 1.5T, T1b ≈ 1.35 s.
- Partial volume correction is important for ASL due to its low resolution (~3–4 mm).
- ASLPrep (https://aslprep.readthedocs.io/) is the recommended automated pipeline for large cohorts.
- This skill is for research workflows; not for clinical decision-making.

## Reference

- Alsop et al. (2015): Recommended implementation of ASL (Magnetic Resonance in Medicine)
- Buxton et al. (1998): General kinetic model for ASL (Journal of Cerebral Blood Flow & Metabolism)
- ASLPrep: https://aslprep.readthedocs.io/
- FSL BASIL: https://fsl.fmrib.ox.ac.uk/fsl/fslwiki/BASIL
- BIDS ASL extension: https://bids-specification.readthedocs.io/en/stable/04-modality-specific-files/11-arterial-spin-labeling.html

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-05-06 12:19 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
