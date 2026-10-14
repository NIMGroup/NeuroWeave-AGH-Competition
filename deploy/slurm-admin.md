# NeuroWeave GPU 节点管理员配置

适用已核查的 `mycluster`：`mgr` 无 GPU，`node01–03` 各一张 RTX 5060 8 GB；Slurm 21.08.5，配置路径 `/etc/slurm/slurm.conf`。脚本只登记 GPU 资源，保留现有 CPU、内存、分区、MUNGE、调度与进程跟踪设置，不安装或升级 Slurm。公共环境已在 `/opt/neuroweave/envs`，2026-10-08 核实 GPU 登记已完成，节点正在承担计算任务。下面初始化/回滚步骤只适用于明确安排的管理员维护窗口；日常状态先用 `sinfo`、`squeue`、`scontrol show config` 和 `scontrol show node` 只读检查。

`scripts/configure_slurm_gpu.py` 应同步到共享目录 `/opt/neuroweave/admin/configure_slurm_gpu.py`。以下命令由 `hipeson` 在 **mgr 的交互终端**执行，`sudo` 密码直接输入服务器终端。脚本每次先检查队列为空；请在维护期间暂停其他用户提交任务，避免检查后出现新作业。

## 维护窗口内的初始化配置

将 `scripts/enable_slurm_gpu.sh` 同步到 `/opt/neuroweave/admin/enable_slurm_gpu.sh` 后，可以在 mgr 的交互终端以 `hipeson` 执行：

```bash
bash /opt/neuroweave/admin/enable_slurm_gpu.sh
```

**这个便捷脚本默认实际执行配置，属于管理员操作，不是 dry-run。** 它依次在 mgr 和三个节点调用 Python 脚本 `--apply`；确认空队列后重启 mgr 的 `slurmctld` 并检查服务活跃，再重启四台机器的 `slurmd` 并逐一检查；最后等待 5 秒，通过 `--finalize` 的完整验证后恢复 GPU 节点调度。任何命令失败立即停止，控制器重启或检查失败时不会继续重启计算节点。已有备份和已完成步骤保留，失败不自动回滚，应根据错误和下方日志、回滚说明处理。

脚本要求可用的 SSH 密钥连接，`sudo` 需要的密码现场输入；不读取、保存或转发密码。如果最终检查显示节点注册尚未完成，核查服务后单独重新执行 `sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py --finalize`。下方第 1–3 节为逐步执行同一流程的方式；便捷脚本成功后，直接进行第 4 节的 GPU 实测即可。

## 1. 查看精确差异

```bash
sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py
ssh -t node01 sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py
ssh -t node02 sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py
ssh -t node03 sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py
```

默认 dry-run，不写配置、不重启服务、不恢复 DOWN 节点。预期只增加全局 `GresTypes=gpu`、三个计算节点各自的 `Gres=gpu:1`，以及计算节点 `/etc/slurm/gres.conf` 的 `Name=gpu File=/dev/nvidia0`。已有非预期 GPU 配置会被拒绝，不会被覆盖。所有主机最终的 `slurm.conf` 非空行必须一致；额外空白行不影响 Slurm 配置，不作为差异拒绝。

## 2. 备份并应用

```bash
sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py --apply
ssh -t node01 sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py --apply
ssh -t node02 sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py --apply
ssh -t node03 sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py --apply
```

备份保存在每台机器的 `/var/backups/neuroweave-slurm/<主机>-<UTC时间>/`，包含原文件和校验清单；写入保留原权限、属主和属组，并逐文件原子替换。应用后仍不会改变服务或节点状态。

## 3. 加载配置，核查节点

确认队列仍为空（`squeue -a -h` 无输出）。首次增加 `GresTypes` 在当前旧版本上采用重启控制器，使该全局设置可靠生效；已有 `GresTypes=gpu` 的后续节点配置更改可以使用 `sudo scontrol reconfigure`。此操作需维护窗口且不应有其他作业提交。

```bash
sudo systemctl restart slurmctld
sudo systemctl is-active slurmctld
sudo systemctl restart slurmd
ssh -t node01 sudo systemctl restart slurmd
ssh -t node02 sudo systemctl restart slurmd
ssh -t node03 sudo systemctl restart slurmd
scontrol show config | grep GresTypes
scontrol show node 'node[01-03]'
```

如果服务失败、节点出现 `DRAIN` / `INVALID_REG` / `NOT_RESPONDING`，先查看该节点 `sudo journalctl -u slurmd -n 100 --no-pager`，以及 mgr 的 `sudo journalctl -u slurmctld -n 100 --no-pager`。不要直接覆盖状态恢复；核查意外重启原因、MUNGE、时间同步、CPU/内存资源是否匹配和 GPU 驱动状态。

确认 `hipeson` 可以无密码 SSH 到三个节点，再执行显式恢复步骤：

```bash
sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py --finalize
```

该命令仅允许 mgr 执行。它以 `hipeson` 的 SSH 身份验证每个节点配置的非空行指纹、GPU 设备与型号、slurmd 活跃且在配置写入后启动、控制器已登记同一 slurmd 实例和 `gpu:1`。仅接受 `DOWN` 或 `IDLE` 状态；全部通过且队列仍为空后，才执行 `scontrol update NodeName=node[01-03] State=RESUME`。注册信息不完整时可以等数秒重新执行，脚本不会越过检查。

配置和服务重启已经完成时，不必重跑完整 wrapper。先用普通账号执行全部最终检查，再执行上面的 `sudo ... --finalize`：

```bash
python3 /opt/neuroweave/admin/configure_slurm_gpu.py --check-finalize
```

`--check-finalize` 不更改节点状态。校验保留所有非空配置行，仅忽略空白行；解析 `scontrol` 输出时，Linux 内核版本中的 `#` 不视为注释。GPU 登记根据 `Gres=gpu:1` 判断；当前未配置 `AccountingStorageTRES=gres/gpu`，因此 `CfgTRES` 未显示 GPU 不代表 GRES 调度未配置。若 `CfgTRES` 显式包含 GPU 数量，则必须与 GRES 一致。

## 4. 从 mgr 提交 GPU 实测

使用共享目录保存脚本、工作目录、日志和数据，例如 `/opt/neuroweave/work`（需提前为任务用户配置写权限）。对三台节点分别验证：

```bash
for node in node01 node02 node03; do
  srun --partition=all --nodelist="$node" --nodes=1 --ntasks=1 \
    --cpus-per-task=2 --mem=4G --time=00:05:00 --gres=gpu:1 \
    --chdir=/opt/neuroweave \
    /opt/neuroweave/envs/torch-cu128-py311/bin/python -c \
    'import os,socket,torch; assert torch.cuda.is_available(); x=torch.randn(1024,1024,device="cuda"); y=x@x; torch.cuda.synchronize(); print(socket.gethostname(),os.getenv("CUDA_VISIBLE_DEVICES"),torch.cuda.get_device_name(0),y.norm().item())'
done
```

这一步实际通过 Slurm 调度并进行 CUDA 计算。主节点是否有显卡不影响提交。仅 `nvidia-smi` 或直接 SSH 的 CUDA 检测成功不能替代本步骤。平台接入需另行使用 Slurm 执行后端，并保证节点可读取代码、输入、模型权重和共享环境，以及写入输出。

## 回滚

在对应主机使用脚本打印的备份路径：

```bash
sudo python3 /opt/neuroweave/admin/configure_slurm_gpu.py --rollback /var/backups/neuroweave-slurm/<该主机的备份目录>
```

脚本要求队列为空，且当前文件未出现后续修改。恢复全部机器配置后，在维护窗口按第 3 节重启对应服务。回滚配置不会撤销已执行的节点 RESUME；如需重新退出调度，应由管理员明确设置节点为 DRAIN 并注明原因。

## 当前限制

2026-10-08 核实 `TaskPlugin=task/none`、`ProctrackType=proctrack/linuxproc`。Slurm 21.08 与当前节点可能使用的 cgroup v2 需要独立验证和版本升级规划，不能直接套用较新 Slurm 的 cgroup v2 配置。`Gres=gpu:1` 能让 Slurm 分配和计数 GPU，并为正常 Slurm 作业设置 GPU 可见性；**尚未建立基于设备 cgroup 的强制隔离**，不能据此承诺阻止用户绕过环境变量或直接 SSH 使用显卡。

参考：[Slurm GRES 配置与 GPU 作业](https://slurm.schedmd.com/gres.html)、[scontrol 节点状态管理](https://slurm.schedmd.com/scontrol.html)、[Slurm cgroup v2 插件](https://slurm.schedmd.com/cgroup_v2.html)。文档可能描述新版能力，本脚本只使用已存在于当前集群的基础配置。
