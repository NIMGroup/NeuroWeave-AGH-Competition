# 方法运行条件与验证

当前平台以独立 Method 契约和注册 Runner 决定执行能力。Skill 用于统筹和选择，软件已安装、文档存在或规划条目被检索到，都不等于方法可以直接运行。

## 执行前检查

1. 方法有真实的软件命令、库 API 或已有脚本入口，并出现在 Runner 登记中。
2. 目标本地环境或 Slurm 节点具备所需软件、Python 包、模型权重、许可证和硬件。
3. 输入满足契约与文件授权；参数条件、端口连接和输出目录有效。
4. 用户检查 Dry-run 结果和未覆盖的科研检查，在工作台点击运行；比赛版不追加审批弹窗。

软件可以来自 `/opt` 的公共环境，目标节点可通过共享 Worker 调用。API 环境和科学环境分离；节点调度及每步资源设置见 [Slurm 执行](../slurm-execution.md)，环境维护见 [公共环境](../python-environments/README.md)。

平台只调用已有实现，不因为 NumPy/PyTorch 可导入就补写缺失算法或训练循环。通用 Shell、安装命令及原始文档片段不会成为执行入口。未完成真实适配的能力继续作为规划参考。

## 当前状态与历史快照

以方法详情、目标节点探测和本次 Dry-run 为准。原生定义数量见 [结构指南](../structured-skill-methods.md)，证据状态见 [质量检查](../catalog-quality.md)。

本目录的 `readiness.json`、`verification.json`、`planning-verification.json` 等记录早期安装与调用验证，保留用于追溯；其中旧 Skill、候选入口或 Runner 数量不再是当前原生目录的统计口径。方法版本和执行依赖变化后，历史成功不自动算作当前已验证。

已接入脚本的来源与哈希见 [实现来源清单](../../bakend/resources/existing_implementation_sources.json)。被撤回的替代实现见 [撤回记录](../../bakend/resources/withdrawn_execution.json)，不能用新描述重新授权它们。

发布必须先比较服务器版本，使用新的固定 Worker 目录并保留活跃任务所用旧目录；不得清理数据库、输入或输出来获得“干净测试”。新增方法维护见 [原生结构](../structured-skill-methods.md)。
