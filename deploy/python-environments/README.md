# 公共 Python 环境

现有科学与 PyTorch 环境已安装并通过共享路径接入 Runner/Slurm。此文描述环境职责和维护步骤，当前软件及服务路径见 [软件路径](../SERVER_SOFTWARE_PATHS.md)和[服务器部署](../server-deployment.md)。

## 环境职责

| 环境 | Python | 用途 |
| --- | --- | --- |
| scientific | `/opt/neuroweave/envs/scientific-py311/bin/python` | NumPy/SciPy/Pandas、统计学习、生存分析、Nilearn/DIPY、MNE、影像格式和可视化 |
| torch | `/opt/neuroweave/envs/torch-cu128-py311/bin/python` | PyTorch CUDA 12.8、MONAI、TorchIO、Lightning、Transformers |

两者使用 Python 3.11，与比赛 API 的 `runtime/hackathon/api-venv` 分离。`/opt` 通过 NFS 对计算节点可见；环境由维护账户管理，其他用户仅可读/执行。`locks/` 保存安装时的 pip 与 Conda 清单，版本变化后重新核对和更新。比赛配置入口见 [执行软件配置](../hackathon/SOFTWARE.md)。

NiBabel 等科学检查由 `LOCALFLOW_SKILL_PYTHON` 指定；Slurm Worker 使用 `LOCALFLOW_SLURM_PYTHON`。安装库不自动创建可执行方法，方法还需要真实入口、Runner、输入输出及资源检查。模板、图谱、预训练权重和许可证须按具体方法准备；TensorFlow 不混装进 PyTorch 环境。

## 日常探测

从当前项目根目录运行：

```bash
PYTHONNOUSERSITE=1 MPLBACKEND=Agg /opt/neuroweave/envs/scientific-py311/bin/python scripts/probe_shared_python.py --profile scientific
PYTHONNOUSERSITE=1 MPLBACKEND=Agg /opt/neuroweave/envs/torch-cu128-py311/bin/python scripts/probe_shared_python.py --profile torch
```

主节点没有 GPU，CPU 探测的 `cuda_available=false` 不表示环境损坏。GPU 计算验证必须在已分配 GPU 的 Slurm 作业中运行 `--profile torch --require-cuda`；`gpu-probe.sbatch.example` 是模板，提交前核对脚本、共享工作目录和输出路径。

`sbatch` 传送作业文本，不传送 Python 脚本、模型或数据。node01–03 各有约 8 GB 显存的 RTX 5060；申请的 `--mem` 是主机内存。提交获得 Job ID 后继续检查状态、退出码与产物，不能把提交成功当作计算成功。

集群已启用 GPU 资源登记和 SlurmDBD 持久记账；cgroup 设备强制隔离尚未启用。调度、共享目录和恢复监控见 [Slurm 执行](../slurm-execution.md)，记账与备份见 [运行维护](../reliability-upgrade.md)。

## 安装与依赖变更

现有部署优先复用环境。确需在新服务器安装时，由管理员创建 `/opt/neuroweave/envs` 并授予指定维护账户权限，再从项目根目录执行：

```bash
bash scripts/install_shared_python.sh scientific
bash scripts/install_shared_python.sh torch
```

安装器拒绝覆盖已有前缀，结束时运行 `pip check` 并写入 `requirements.lock.txt`、`conda-explicit.txt`。中断后先诊断原因，不能反复删除重建或扩大私人 Conda 父目录权限。环境变更前检查活跃作业的依赖，变更后分别做导入、CPU 合成测试、Slurm GPU 验证及受影响方法的执行回归。

初始 GPU/调度验证的结构化记录保存在 `../slurm-test-results.json`，软件与方法证据保存在 `../method-verification/`。这些记录绑定当时版本，不是当前所有方法/参数组合已验证的证明。

官方参考：[Slurm sbatch](https://slurm.schedmd.com/sbatch.html)、[Slurm GRES](https://slurm.schedmd.com/gres.html)、[PyTorch 版本](https://pytorch.org/get-started/previous-versions/)。
