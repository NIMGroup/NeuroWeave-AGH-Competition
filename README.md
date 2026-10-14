# NeuroWeave · AGH 科研工作台

本仓库独立保存 NeuroWeave 比赛版本，默认分支为 `main`。NeuroWeave 提供神经影像与数据处理的业务工作台，Agnes Harness（AGH）负责会话、Agnes 模型循环、工具调度和上下文管理；用户配置输入与参数后，通过 AGH 注册服务提交到 Slurm，在服务器真实执行。

初始源码来自 [NeuroWeave 的 AGH-AGNES 分支](https://github.com/NIMGroup/NeuroWeave/tree/d281f803fd634ab76f3f0000e5f166207bf4cab4)，来源提交为 `d281f803fd634ab76f3f0000e5f166207bf4cab4`。来源与依赖提交记录在 [SOURCE_VERSION.json](SOURCE_VERSION.json)，原始实现的 attribution 和许可证保留。本仓库后续使用自己的提交号发布 Worker 和交付源码，不会自动更新原项目或服务器。

## 从这里开始

| 需求 | 说明 |
| --- | --- |
| 安装、配置和启动平台 | [平台运行说明](deploy/hackathon/README.md) |
| 配置 MRtrix3、FSL、ANTs、FreeSurfer、Python 和 Slurm | [执行软件配置](deploy/hackathon/SOFTWARE.md)、[非密钥配置模板](deploy/hackathon/science-paths.env.example) |
| 了解 AGH 与原有业务服务的分工 | [架构与执行链](deploy/hackathon/ARCHITECTURE.md) |
| 整理视频、执行记录和提交材料 | [提交材料说明](deploy/hackathon/SUBMISSION.md) |
| 开发、检查和生成源码包 | [开发与交付检查](deploy/hackathon/VALIDATION.md) |
| 维护 Method / Skill / Pipeline | [业务文档索引](deploy/README.md) |

完整服务器运行需要 Linux x86_64、Agnes 服务访问权限、Slurm 集群，以及本次方法所需的科学软件。Windows 可以作为浏览器和 SSH 客户端；仅运行前端不能完成服务器计算。

## 使用流程

1. 打开 AGH 页面，新建会话、选择 AGH 工作区，点击输入区的 **NeuroWeave 工作台**。
2. 选择服务器数据集和输出目录。这些选择绑定当前会话，可在发送第一条消息前完成。
3. 描述目标。AGH 检索真实方法契约并生成可编辑草稿；输入文件、参数和资源在工作台选择。
4. 在“流程与运行”完成输入绑定与 Dry-run，选择 Slurm 分区、节点和每样本资源，然后点击运行。
5. 在工作台查看真实进度、日志、产物及历史记录。需要模型解释或核验时，在同一 AGH 会话要求查询该运行 ID 并验证结果。

比赛版固定 **Slurm + 按样本运行**，工作台配置和执行不追加审批弹窗；实际执行仍由用户点击运行、停止、重试或文件操作按钮发起。数据集可浏览服务器账户有权读取的目录，输出与原地改动受部署的输出范围限制。

工作台包含 Skill / Method 目录、Pipeline 画布、输入映射、资源配置、任务目标及流程修订、运行监控和数据操作。工作区和会话的改名、移除、删除与恢复位于 AGH 左侧“工作区与会话管理”。移除列表记录不删除服务器科研目录。

当前静态目录登记 329 个 Method、105 个规范 Skill 和 24 个 Pipeline；登记数量不代表每个方法都已安装或通过科研验证。实际可用性以目标环境、方法契约和本次预检为准。

## 当前实现边界

模型规划调用 AGH 原生工具；工作台运行通过 AGH 注册的 `nw.action.submit` 服务提交，Runner 调用已登记的软件或脚本。服务提交与模型工具调用分别记录。业务 API 不开放任意命令执行。

工作台自动监控计算状态；当前没有“运行完成后自动唤起 AGH 验证”的功能。同一会话可以通过 `nw_read_run` 和 `nw_verify_run` 查询与核验。文件存在、哈希一致与方法专项验证须分别解释，不能据此宣称临床有效。

AGH 官方源码固定为 `dc267c3c78429933e2b3d8c5d544e56bb980a574`，版本锁见 [lock.json](integrations/agh/lock.json)。业务基准为服务器 3003 在 2026-10-14 14:11（北京时间）的 `7ecf540296136a10bf2a39fb4342b098861dea33`，见 [baseline.json](integrations/agh/baseline.json)。本仓库不随其他站点自动更新。

## 源码结构

```text
frontend/                 AGH 嵌入工作台、3004 静态入口和可复用业务组件
integrations/agh/          AGH 插件、工具契约、提示词及 Web 适配
bakend/app/                FastAPI 业务服务、流程、文件接口和可信 Runner
methods/                  方法版本、参数、条件输入输出与执行契约
skills/                   专业指导、登记参考资料和已有实现脚本
pipelines/                内置有向无环流程
models/                   已登记方法引用的已有模型实现
scripts/hackathon/        比赛版依赖安装、准备、配置和启动
deploy/hackathon/         比赛版运行、软件配置与材料说明
THIRD_PARTY_NOTICES/       第三方许可证与来源声明
```

`bakend` 是实际目录名。普通平台代码仍供共享业务和回归使用；比赛版入口是 `scripts/hackathon/server-*.sh`，不使用根目录普通站点的 Compose 配置。数据库、AGH 会话、密钥、依赖环境和科研数据位于 Git 忽略的运行目录，均不包含在源码包中。

来源与许可证见 [UPSTREAM.md](UPSTREAM.md) 和 [第三方声明](THIRD_PARTY_NOTICES/)。
