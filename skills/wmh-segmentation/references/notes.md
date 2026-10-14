# wmh-segmentation：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## 分割输入与质量检查

影像模态、空间、分辨率和强度处理需要符合所选模型的输入条件。对分割结果检查标签和空间，并与输入影像叠加查看漏分、误分及边界；输出文件存在不证明科研质量合格。

保留输入版本、模型版本及处理记录，避免把不同配置的分割结果直接作为可比较测量。科研分割结果不等于临床诊断。

## 背景来源

原说明使用 [MARS-WMH](https://github.com/miac-research/MARS-WMH)。模型、容器、资源和可用入口以当前方法及执行环境为准；文档不触发权限修改、环境安装或容器启动。

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-03-26 00:29 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
