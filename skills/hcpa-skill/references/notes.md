# hcpa-skill：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### Source
Current HCP-A/AABC data is distributed through **ConnectomeDB powered by BALSA**:
- Release page: https://www.humanconnectome.org/study/hcp-lifespan-aging/data-releases
- Current release: AABC Release 2 (2026-01-28)
- Register for BALSA and accept the AABC Data Use Terms. An academic, nonprofit, or government email address is required.
- Imaging packages transfer through IBM Aspera Connect. Select a modality package or subject subset and calculate storage before transfer.

## HCP-A Task Paradigms

| Task | Description |
|---|---|
| VISMOTOR | Simultaneous visual and motor activation paradigm |
| CARIT | Conditioned Approach Response Inhibition Task |
| FACENAME | Face-name paired-associates memory task |
| REST | Resting-state functional MRI |

## Important Notes and Limitations

- HCP-A complements HCP-YA to cover the full adult lifespan (22-100 years).
- HCP-A processing is resource intensive; plan storage and compute accordingly.
- The HCP-A/AABC cohort is designed around typical aging; do not infer a clinical impairment cohort from age alone.
- Do not mix legacy Lifespan 2.0 packages with AABC Release 2 without documenting and harmonizing release-specific processing differences.
- `hcpa-skill` is orchestration-only; detailed preprocessing logic remains in modality skills.

## Reference

- HCP Aging: https://www.humanconnectome.org/study/hcp-lifespan-aging
- AABC Release 2: https://www.humanconnectome.org/study/hcp-lifespan-aging/data-releases
- HCP-A task protocols: https://www.humanconnectome.org/study/hcp-lifespan-aging/project-protocols
- Bookheimer et al. (2019): The Lifespan Human Connectome Project in Aging

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill - freely modifiable within the project)。
原说明更新时间：2026-08-11 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
