# 文档索引

本分支的运行入口是 AGH 服务器比赛版。先阅读 [平台运行说明](hackathon/README.md) 和 [执行软件配置](hackathon/SOFTWARE.md)，再按需求查阅业务细节。方法定义、注册 Runner 与本次预检决定实际可运行能力。

## 比赛版交付

| 主题 | 文档 |
| --- | --- |
| 源码入口 | [README](../README.md) |
| 安装、模型配置、启动、SSH、3004 | [平台运行说明](hackathon/README.md) |
| 专业软件、Python、许可证和 Slurm | [执行软件配置](hackathon/SOFTWARE.md) |
| AGH 与业务服务分工、执行与验证边界 | [架构说明](hackathon/ARCHITECTURE.md) |
| 视频、真实工具链、测试样例和公开材料 | [提交说明](hackathon/SUBMISSION.md) |
| 本地检查、真实证据和源码包 | [开发与交付检查](hackathon/VALIDATION.md) |
| 软件路径与依赖基线 | [软件路径](SERVER_SOFTWARE_PATHS.md)、[Python 环境](python-environments/README.md) |

## 共享业务维护

| 主题 | 文档 |
| --- | --- |
| Skill / Method / Pipeline 契约 | [原生结构](structured-skill-methods.md)、[Skill 参考](skill-references.md)、[专业指导](skill-guidance.md) |
| 目录、画布与参数 | [资源库](native-library.md)、[Pipeline 画布](pipeline-canvas-ui.md)、[参数与预设](pipeline-parameters-and-presets.md) |
| 任务与方案 | [任务管理](task-management.md)、[流程裁剪](workflow-scope-editing.md)、[Pipeline 删除恢复](published-pipeline-deletion.md) |
| 数据与输出 | [目录范围](workspaces.md)、[数据操作](data-assistant.md)、[数据 Python 容器](data-python/README.md) |
| 历史输入与科研结果 | [历史输入](historical-run-inputs.md)、[质量检查](catalog-quality.md) |
| Runner 与集群 | [Slurm 执行](slurm-execution.md)、[方法运行条件](skill-runtime/README.md)、[FreeSurfer 恢复](freesurfer-recovery.md) |
| 扩展与来源 | [代码维护](code-architecture.md)、[来源记录](../UPSTREAM.md)、[第三方声明](../THIRD_PARTY_NOTICES/) |

普通平台的协调者、功能 Agent、聊天 UI 与原站点部署指南保留供共享代码维护和历史追溯；比赛实例的模型循环、审批、启动方式及结果反馈以 hackathon 目录为准。旧六工具合成演示及其材料生成器已移除，历史代码仍可通过 Git 查询。

software-contracts、method-verification 和 Python locks 保存原始帮助、实现依据及带版本的历史检查。它们被方法目录与运行依赖引用，保留用于追溯；不将历史记录视作当前服务器全部方法已验证。
