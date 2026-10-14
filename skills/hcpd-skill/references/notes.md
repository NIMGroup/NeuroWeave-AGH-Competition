# hcpd-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
HCP-D data is distributed through **ConnectomeDB**:
- Website: https://db.humanconnectome.org/
- Requires ConnectomeDB account and data use agreement
- Part of the HCP Lifespan initiative

## HCP-D Task Paradigms

| Task | Description | Duration |
|---|---|---|
| MOTOR | Finger tapping, toe movement, tongue movement | ~3 min |
| EMOTION | Faces and shapes matching | ~2 min |
| GAMBLING | Card guessing with reward/loss | ~3 min |
| LANGUAGE | Story comprehension and math | ~4 min |
| RELATIONAL | Relational reasoning matching | ~3 min |
| SOCIAL | Social cognition (mentalizing) movie clips | ~3 min |
| WM | Working memory (faces, places, tools, body parts) | ~5 min |
| REST | Resting-state (eyes open) | ~15 min × 4 runs |

## Important Notes and Limitations

- HCP-D covers ages 5-21 years; pediatric processing may require age-specific templates and atlases.
- Head motion is typically higher in pediatric populations; QC thresholds may need adjustment.
- HCP-D complements HCP-YA (22-35) and HCP-A (36-100) to cover the full lifespan.
- `hcpd-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- HCP Development: https://www.humanconnectome.org/study/hcp-lifespan-development
- ConnectomeDB: https://db.humanconnectome.org/
- Somerville et al. (2018): The Lifespan Human Connectome Project in Development

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-05-06 13:02 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
