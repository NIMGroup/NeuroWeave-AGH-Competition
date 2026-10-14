# NeuroWeave Slurm 执行

本比赛分支固定使用 Slurm + 按样本运行。用户在工作台配置输入和参数，完成 Dry-run，选择分区、节点、CPU、内存、GPU 和时限后点击运行，无额外审批。流程中每个实际执行步骤提交一个单节点作业；前一步成功且产物验证通过，才提交下一步。当前不支持一个步骤跨节点分布式训练。安装和配置入口见 [平台运行说明](hackathon/README.md) 与 [执行软件配置](hackathon/SOFTWARE.md)。

## 指定节点与按样本并行

- 比赛工作台不显示 local / slurm 或样本运行方式切换；节点必须由用户选择，通过既有 sbatch --nodelist 提交。
- “按样本运行”：支持一个或多个样本。原数据集只有一个样本时，直接使用已选文件、目录或历史输入，并选择一台节点；不要求多样本路径模板。多个样本需要逐样本输入绑定，再勾选一台或多台节点。每台节点分别设置并行样本数（默认 1）、CPU、主机内存、GPU 和每步时限。
- 单样本固定按样本运行并选择一台可用节点，运行记录保留样本标识。原多样本数据集筛选后只剩一个样本时，仍保留原有输入身份和模板检查。
- 按数据集已保存的样本顺序轮流分配到所选节点；各节点最多同时处理设定数量的样本，同一样本内部按流程顺序执行。并行数量受本节点分到的样本数约束，界面显示实际并行上限和资源合计。每台节点可设置 1–128，一次运行的实际并行样本总数最多 128。
- CPU、主机内存和 GPU 按每个样本的每一步申请，乘以实际并行上限后不得超过节点总容量。例如 node01 设置并行 4、CPU 8、内存 16 GB，将最多申请 32 核和 64 GB。繁忙时仍由 Slurm 排队；并行样本数是上限，不保证立即获得资源。
- 每个样本使用独立步骤标识和输出目录，上游产物只连接到同一样本的下游。FreeSurfer 等已有执行配方内部仍按原有工作目录约定运行。
- 单个样本失败时停止其下游步骤，其他样本继续。取消会传递给所有正在执行的节点；服务重启或监控中断保留各个 Job ID 并恢复监控，不重复提交已记录的作业。
- 整个数据集级别的输入不能自动当成各样本输入；需要有效、完整的样本清单及路径模板。共享参考文件可复用，指向某一样本的单文件绑定需要改为各样本匹配，避免重复处理同一份数据。

资源上限同时受平台和实际节点容量约束。平台支持最多 128 核、512 GB 内存、1 张 GPU、1440 分钟；实际节点不足时拒绝提交。2026-10-08 核实 node01–03 的 Slurm 配置为 245000 MB，可申请的整 GB 内存上限为 239 GB，mgr 为 478 GB；具体提交仍读取当前节点容量。方法中已有的线程参数保持不变，申请核数必须不少于线程参数；资源按每个步骤申请，忙碌时排队。

多节点请求使用原运行接口，例如：

```json
{
  "expected_revision": 3,
  "execution": {
    "backend": "slurm",
    "partition": "all",
    "sample_mode": "per_sample",
    "nodes": [
      {"node": "node01", "parallel_samples": 4, "cpus": 8, "memory_gb": 16, "gpus": 0, "time_minutes": 120},
      {"node": "node02", "parallel_samples": 2, "cpus": 16, "memory_gb": 32, "gpus": 0, "time_minutes": 120}
    ]
  }
}
```

运行记录的每一步包含 sample_id、sample_label 和 node_name，并沿用原有 scheduler 日志。节点资源来自 sinfo 查询，客户端选择后服务端再次校验名称、分区和容量；不接受任意主机表达式或 shell 片段。

独立合成数据验证：在已有非密钥 Slurm 环境设置下运行 scripts/smoke_slurm_execution.py --sample-nodes node01 node02 --sample-concurrency 2。它使用独立 SQLite 库和样本目录，验证每节点并行配置、真实 Worker 输出、节点及实际 CPU 分配，不读取生产数据库或研究数据。

## 主节点不需要 GPU

mgr 上的 API 只负责 `sbatch` 提交、状态查询、结果登记和 `scancel`。选择 GPU=1 后会请求 `--gres=gpu:1`，由 Slurm 将任务安排到已登记 GPU 的 node01–03。Worker 在计算节点检测输入、软件和 GPU，再调用既有可信 Runner。分区资源设置不会改变已经确认的方法参数；方法的线程参数不得超过申请的 CPU 数。

`Slurm` 分配 GPU 并设置 `CUDA_VISIBLE_DEVICES` 的行为见 [Slurm GRES 官方说明](https://slurm.schedmd.com/gres.html)。本集群的 GPU 注册及节点恢复见 [管理员步骤](slurm-admin.md)。当前配置不新增 cgroup 设备隔离，不能据此声称阻止用户通过直接 SSH 占用其他作业的设备。

## 共享路径和部署配置

计算节点的 `/opt` 来自 mgr 的 NFS，`/home/data` 来自公共存储。源码目录位于 mgr 的账户目录；计算节点使用 `/home/data/neuroweave/worker/<部署提交>` 中的固定代码，输入和输出也使用共享路径。

配置由各站 runtime 环境文件加载，实际文件及输出根见 [服务器部署](server-deployment.md)。以下为非密钥模板，路径须按目标部署设置：

```dotenv
LOCALFLOW_SLURM_ENABLED=true
LOCALFLOW_SLURM_WORKER_ROOT=/home/data/neuroweave/worker/<发布提交>
LOCALFLOW_SLURM_PYTHON=/opt/neuroweave/envs/scientific-py311/bin/python
LOCALFLOW_SLURM_OUTPUT_ROOT=/home/data/your-project/results
LOCALFLOW_SKILL_PYTHON=/opt/neuroweave/envs/scientific-py311/bin/python
LOCALFLOW_DIPY_PYTHON=/opt/neuroweave/envs/scientific-py311/bin/python
```

Worker 发布目录包含 `bakend/app`、`bakend/resources`、`skills`、`methods`、`pipelines` 及被方法引用的实现，不包含 `.env`、数据库或应用运行目录。已提交作业引用固定发布路径；旧版本在相关作业全部结束之前必须保留。公共 Python 环境对用户可读可执行，作业目录与结果默认仅 hipeson 可访问。环境变量使用显式白名单，提交脚本清理登录环境，模型 API 密钥不会复制到作业文件。

每个站点使用一个 FastAPI 进程和自己的 SQLite 数据库，进程内锁防止重复提交和取消时覆盖 Job ID。不要直接增加 Uvicorn workers 或启动第二个 API 实例访问同一数据库；多进程部署需要数据库层的任务领取与唯一约束改造。

## 状态、取消和恢复

- `GET /api/execution/backends`：分区、节点、GPU 可调度状态及不可用原因。
- `POST /api/workflows/{id}/runs`：在原 `expected_revision` 外增加 `execution` 对象。
- `GET /api/runs/{id}`：返回运行资源及每步 `scheduler.job_id/state/node_list/reason`。
- 原取消接口调用 `scancel`；确认远端终态后才显示已取消。
- API 正常关闭会停止监控并保留 Slurm 作业，启动后从持久 Job ID 恢复。不得把恢复监控理解为重新执行。
- 临时监控中断时保持任务活动状态。可调用 `POST /api/runs/{id}/resume-monitoring` 重连，或者使用取消按钮。
- 网页的“恢复监控”按钮调用同一恢复接口；重复点击不会再次提交已有作业。

请求示例：

```json
{
  "expected_revision": 3,
  "execution": {
    "backend": "slurm", "partition": "all", "cpus": 4,
    "memory_gb": 16, "gpus": 1, "time_minutes": 60
  }
}
```

成功必须同时具备 Slurm 的 COMPLETED/ExitCode=0:0、Worker 原子结果文件，以及经过路径和完整性验证的非空产物。调度器失败、超时、OOM 或 Worker 失败均不能登记成功。

本集群已在 2026-09-08 配置 SlurmDBD 持久记账并通过 `sacct` 检查，见 [记账与运行维护](reliability-upgrade.md)。后端也持久保存查询到的终态；若某次运行缺少可靠调度终态，则保留待核查状态，不依据单独结果文件推断成功。启用记账之前没有存储的历史不能补造。提交中断后只有提交标记而没有 Job ID 时，也禁止自动重提；按日志中的 `nw-<标记>` 核查队列及作业文件。

## 深度学习脚本与规划型 Skill

GPU Python 位于 `/opt/neuroweave/envs/torch-cu128-py311`。下面的模板支持从 mgr 提交用户自己的训练或推理脚本；数据、脚本、日志与检查点必须放在共享目录，模型权重需要自行准备：

```bash
#!/usr/bin/env bash
#SBATCH --job-name=neuroweave-training
#SBATCH --partition=all
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --gres=gpu:1
#SBATCH --time=01:00:00
#SBATCH --chdir=/home/data/your-project
#SBATCH --output=/home/data/your-project/slurm-%j.out
set -euo pipefail
export PYTHONNOUSERSITE=1
export OMP_NUM_THREADS="$SLURM_CPUS_PER_TASK"
srun /opt/neuroweave/envs/torch-cu128-py311/bin/python train.py
```

保存后在 mgr 运行 `sbatch train.sbatch`；通过 `squeue -u "$USER"` 查看，通过 `scancel <Job ID>` 取消。每个节点只有一张约 8 GB 显存的 RTX 5060，模型大小、batch size 和训练方案必须适应该显存；申请的 `--mem` 是主机内存，不会增加显存。

安装 Python 库和 Slurm 支持不会自动把规划型 Skill 变为可执行方法。未注册的深度学习方法仍需实现并审查 Runner、结构化输入输出、权重和参数验证后，才能由网页运行；平台不接受模型生成的任意 shell/Python 命令。
