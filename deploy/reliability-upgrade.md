# 运行监控、重试与备份维护

运行记录、日志、后台检查和备份各自持久保存。下述功能属于当前 main；服务路径与集群配置见 [服务器部署](server-deployment.md)。

## 运行与重试

| 功能 | 当前行为 |
| --- | --- |
| 失败/取消后重试 | 用户选择未完成步骤或某步骤及后续，生成新 Run 并保留原记录；可以指定样本。状态不确定的 Slurm 作业应先恢复监控，避免重复提交。 |
| 成功步骤复用 | 核对输入哈希、方法及执行依赖指纹、输出路径/大小/哈希和必要上游。缺少证据、文件改变或目录无法形成可靠快照时重新执行所需步骤。 |
| 日志游标 | 初始读取最新日志，按 event_id 增量拉取；历史翻页和回到实时独立控制，不随轮询跳走。 |
| 后台检查 | 扫描、重扫及 Dry-run 返回检查 ID，可查询进度和协作取消；检查提交前复核流程修订，防止旧结果覆盖新参数。 |
| 增量运行存储 | 流程快照与步骤状态分别保存，轮询读取变更步骤。终态和原始日志继续完整保留。 |
| 节点样本队列 | 每台节点在并行上限内领取下一样本，同一样本遵守步骤依赖，不自动搬迁已分配样本。 |
| 资源草稿 | 节点、CPU、内存、GPU、时限和并行配置持久保存；多页面更新检查草稿版本。 |

复用成功步骤不等于软件内部任意断点续算。本地执行进程可能随 API 停止而取消；Slurm 使用固定 Worker 和持久 Job ID，恢复监控重新关联既有作业，不重新提交。后台检查或对话取消在安全检查点生效，已经运行的外部调用可能需等待退出或超时。

## Slurm 持久记账

集群当前使用 Slurm 21.08.5、SlurmDBD 与 MariaDB，`AccountingStorageType=accounting_storage/slurmdbd`、`JobAcctGatherType=jobacct_gather/linux`。可只读检查：

```bash
scontrol show config
sacct -j <待核对的JobID> --parsable2 --format=JobID,JobName,State,ExitCode,Elapsed,AllocCPUS,NodeList
```

应用 Run、Worker 结果与 Slurm 终态相互核对。启用记账之前没有存储的历史不能补造。记账记录资源用量，不提供 CPU、内存或 GPU 的强制隔离；当前 task/none 与 linuxproc 的边界见 [Slurm 管理](slurm-admin.md)。

凭据仅保存在服务器受限配置。SQLite 备份不包含 MariaDB 数据；记账数据库应采用独立 SQL 备份策略，在线数据库不要直接复制物理数据文件。已有 GPU/记账配置已完成，旧管理员初始化脚本不作为日常检查命令。

## SQLite 备份与保留

主站使用 `neuroweave-backup.service` 和 `neuroweave-backup.timer`，每天服务器时间 02:30 左右运行。2026-10-08 核实定时器处于 active/waiting，目标是 `/home/hipeson/NeuroWeave/runtime/localflow.db`，备份写入 `/home/data/neuroweave/backups/hipeson`；开发副本的备份需独立核查。

`scripts/maintain_storage.py` 使用 SQLite Backup API 生成一致备份，执行完整性、外键和独立文件恢复校验。当前定时器保留最近 14 份并过期 14 天前的后台检查记录；科研输入、输出和 Run 审计不自动清理。受限备份目录和文件分别为 0700、0600。

```bash
systemctl --user status neuroweave-backup.timer
/home/hipeson/NeuroWeave/.venv/bin/python /home/hipeson/NeuroWeave/scripts/maintain_storage.py verify --backup /具体路径/nw-db-时间戳.sqlite3
/home/hipeson/NeuroWeave/.venv/bin/python /home/hipeson/NeuroWeave/scripts/maintain_storage.py restore --backup /具体路径/nw-db-时间戳.sqlite3 --destination /独立验证目录/restored.sqlite3
```

`restore` 只创建新文件，目标存在即拒绝覆盖。生产恢复需暂停写入和后台检查、停止目标 API、验证备份并保存当前数据库，再由管理员切换文件；同时处理 WAL，不在活跃进程旁替换数据库。

备份不包含 MRI 数据、实际输出、软件环境或模型权重，共享存储仍需自己的快照/灾备。存储页面展示磁盘余量、最近备份和运行目录统计；清理备份与删除科研结果是独立操作。

## 当前边界与维护验证

平台使用单一后端账户，没有用户级对象归属或多租户隔离。输入授权、文件身份、Method 条件和有限的图像头检查可以阻止部分错误，但没有完整跨方法空间推断或自动科研 QC；影像头一致和命令退出 0 都不能替代科研审阅。

监控、重试、资源和备份变更后，运行对应 RunService、Slurm、后台操作、存储及前端回归；完整检查方式见 [项目说明](../README.md)。科学执行证据另按 [目录质量](catalog-quality.md) 更新。发布前检查应用任务、文件操作和外部集群作业，按当次版本差异制定切换与恢复清单。
