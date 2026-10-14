# hcpya-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
The current HCP-YA release is distributed through **ConnectomeDB powered by BALSA**:
- Release: https://www.humanconnectome.org/study/hcp-young-adult/document/hcp-young-adult-2025-release
- Platform: https://balsa.wustl.edu/ (open the ConnectomeDB tab)
- Register and accept the applicable HCP data-use terms before downloading.
- Restricted non-imaging variables require the corresponding restricted-data approval.

## HCP-YA Task Paradigms

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

- HCP-YA processing is resource intensive (CPU, RAM, and storage).
- HCP-YA releases span many large packages; estimate storage from the selected BALSA packages before transfer.
- The 2025 release reports processed data for 1,071 subjects and unprocessed imaging for 1,113 subjects; "HCP1200" is the cohort/release name, not a complete-case count.
- Age range: 22-35 years.
- `hcpya-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- HCP-YA: https://www.humanconnectome.org/study/hcp-young-adult
- HCP-YA 2025 release: https://www.humanconnectome.org/study/hcp-young-adult/document/hcp-young-adult-2025-release
- HCP data-use terms: https://www.humanconnectome.org/study/hcp-young-adult/data-use-terms
- Glasser et al. (2013): The Human Connectome Project minimally preprocessed pipelines

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-08-11 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
