# 执行软件路径基线

比赛版的安装与配置集中在 [执行软件配置](hackathon/SOFTWARE.md)。下面保留契约与历史证据对应的软件路径，实际部署可通过 runtime/server/science-paths.env 覆盖。

| 用途 | 路径或版本基线 |
| --- | --- |
| API Python | 当前源码目录 runtime/hackathon/api-venv/bin/python |
| 官方 AGH 与 Node | runtime/hackathon/agnes-harness、runtime/hackathon/tools/node |
| 状态与模型 secret store | 当前实例 runtime/server、runtime/server/agh-home |
| 不可变 Worker | NW_SERVER_WORKER_ROOT 指向的共享提交目录 |
| ANTs 2.6.5 | /opt/software/ants-2.6.5 |
| FSL 6.0.7.22 | /opt/software/fsl |
| MRtrix3 3.0.8 | /opt/software/mrtrix3-master |
| FreeSurfer 7.4.1 | /opt/software/freesurfer-7.4.1/usr/local/freesurfer/7.4.1 |
| 科学 Python | /opt/neuroweave/envs/scientific-py311/bin/python |
| 可选 PyTorch Python | /opt/neuroweave/envs/torch-cu128-py311/bin/python |

科学软件、Python、输入、输出和 Worker 必须在所选 Slurm 节点上使用相同绝对路径。这些路径是部署基线，源码包不携带安装目录、许可证、模型权重或实际数据。

软件存在不表示全部命令已接入或科研结果已经验证。Runner 使用明确登记的入口和参数映射，实际方法可用性以当前环境与 Dry-run 为准。依赖维护见 [Python 环境](python-environments/README.md)，原始帮助与方法证据保留于 software-contracts 和 method-verification。
