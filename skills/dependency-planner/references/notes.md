# dependency-planner：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## 依赖条件与验证

区分缺少软件包、版本不兼容、系统库缺失和硬件条件不足。GPU 相关能力还依赖驱动及相应运行库，包安装成功不证明计算可用。

依赖方案需要记录目标平台、版本来源、约束和验证方式；不要依据旧环境示例推断当前服务器配置。环境变更计划与已经完成的安装、导入检查及真实运行是不同证据。

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-04-05 02:01 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
