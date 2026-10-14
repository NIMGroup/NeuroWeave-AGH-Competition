# experiment-controller：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## 实验记录与比较

实验比较需要记录数据版本、样本划分、随机种子、方法版本、超参数、指标定义、运行耗时和异常。消融实验应说明与基线相比仅改变了哪些因素，避免把数据变化误当成方法改进。

汇总多次实验时保留每次测量的来源和时间；失败记录也有助于解释样本排除和资源限制。记录位置以当前任务的输出配置为准。

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-04-05 02:01 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
