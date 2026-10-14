# 执行软件配置说明

NeuroWeave 的流程规划使用登记的方法契约，无需安装专业软件、准备真实数据或连接 Slurm。仅规划部署使用 [planning.env.example](planning.env.example)，设置 NW_SERVER_MODE=planning；AGH 与 Agnes 模型服务仍须可用。

实际执行调用已经安装的专业软件、库 API 和已登记脚本，部署模式为 NW_SERVER_MODE=execution。平台依赖安装器不安装 MRtrix3、FSL、ANTs 或 FreeSurfer；按实际演示方法准备所需软件即可。下面版本来自登记的方法契约和现有部署基线，不表示自动支持新版本的全部参数。

## 配置位置

复制 [science-paths.env.example](science-paths.env.example)到 `runtime/server/science-paths.env`，编辑实际路径。启动脚本自动加载此文件；格式是 Bash 环境变量，路径含空格时使用引号。文件只放软件、Python、许可证和容器映射，Agnes Key 由 AGH 私有配置管理。

复制 [deployment.env.example](deployment.env.example)到 `runtime/server/deployment.env`，填写数据、输出、Worker 与科学 Python。默认状态根为项目内 `runtime/server`；修改状态根须在执行启动脚本前设置 `NW_SERVER_STATE`，且限于当前项目 runtime 下的独立子目录，AGH_HOME 为该目录内的 agh-home。

## 命令行科学软件

| 软件与契约基线 | 配置变量 | 指向位置 | 入口示例 |
| --- | --- | --- | --- |
| MRtrix3 3.0.8 | `LOCALFLOW_MRTRIX_ROOT` | 含 bin/ 的安装根 | bin/tckgen、bin/mrconvert |
| FSL 6.0.7.22 | `LOCALFLOW_FSL_ROOT` | 含 bin/ 的安装根 | bin/fslmaths、bin/flirt、bin/bet |
| ANTs 2.6.5 | `LOCALFLOW_ANTS_ROOT` | 含 bin/ 的安装根 | bin/antsRegistration、bin/antsApplyTransforms |
| FreeSurfer 7.4.1 | `LOCALFLOW_FREESURFER_HOME` | 含 bin/ 和 SetUpFreeSurfer.sh 的实际根 | bin/recon-all、bin/mri_convert |
| DICOM 转换 | `LOCALFLOW_DCM2NIIX`（可选） | dcm2niix 可执行文件 | 默认为 FSL 根下 bin/dcm2niix |
| MRtrix PNG 依赖 | `LOCALFLOW_MRTRIX_LIBPNG` | 当前系统 libpng16.so.16 | 按目标发行版设置 |

获取与安装遵循各软件官方资料：[MRtrix3](https://www.mrtrix.org/download/)、[FSL](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/)、[ANTs](https://github.com/ANTsX/ANTs/releases)、[FreeSurfer](https://surfer.nmr.mgh.harvard.edu/fswiki/DownloadAndInstall)。安装版本应与所选方法契约一致；更换版本后核对帮助文本、参数与方法输出。

配置根目录而非仅修改登录 Shell 的 PATH。Runner 构造实际调用入口和环境；Slurm 作业通过显式白名单传递所需配置。API 账户及计算节点账户都须有读取和执行权限。

FreeSurfer 需部署者自行获取有效许可证，`LOCALFLOW_FREESURFER_LICENSE` 指向许可证文件。部分方法还需要图谱、模板和已有结果，按方法输入槽选择；许可证正文、私有模型权重和科研输入不随源码分发。系统依赖可按需要配置 `LOCALFLOW_FREESURFER_BC`、`LOCALFLOW_FREESURFER_LIBGOMP` 和 `LOCALFLOW_FREESURFER_LIBQUADMATH`。

## Python 环境

| 环境 | 配置 | 职责 |
| --- | --- | --- |
| API Python | runtime/hackathon/api-venv/bin/python | FastAPI 与平台服务，由 API 锁文件安装 |
| 科学 Python | NW_SERVER_SCIENTIFIC_PYTHON；LOCALFLOW_SKILL_PYTHON / LOCALFLOW_DIPY_PYTHON | Slurm Worker、影像检查、NumPy / pandas / NiBabel / DIPY / EEG 等登记脚本 |
| PyTorch Python（按方法需要） | LOCALFLOW_TORCH_PYTHON | 已登记的 Torch / MONAI 等实现，另需对应权重与 GPU |

API 与科学环境分别维护。科学环境的输入依赖清单和已保存版本锁见 [公共 Python 环境](../python-environments/README.md)。新机器可由管理员按该说明安装，已有集群优先使用已准备环境。安装器拒绝覆盖现有前缀。

从项目根目录进行环境探测：

```bash
PYTHONNOUSERSITE=1 MPLBACKEND=Agg /opt/neuroweave/envs/scientific-py311/bin/python scripts/probe_shared_python.py --profile scientific
```

用本机实际 Python 路径替换示例。Torch 探测选择 `--profile torch`；GPU 验证在已有 Slurm 分配的 GPU 节点执行 `--require-cuda`。导入检查只是环境检查，具体算法的结果验证另行记录。

## Slurm 和共享文件系统

科学运行固定 Slurm + 按样本运行。平台不负责安装或管理 Slurm 集群。提交节点须具有 `sbatch`、`squeue`、`sacct`、`scancel`、`sinfo`；集群应提供可靠终态查询。参考 [Slurm 官方 sbatch](https://slurm.schedmd.com/sbatch.html)与[平台调度说明](../slurm-execution.md)。

必须配置：

- `NW_SERVER_WORKER_ROOT`：同一提交的不可变共享 Worker，含 bakend/app、bakend/resources、skills、methods、pipelines、models 和 revision.txt。
- `NW_SERVER_OUTPUT_ROOT`：该比赛实例独占的共享输出根，可由 API 与计算节点读写。
- `NW_SERVER_SCIENTIFIC_PYTHON`：所有计算节点可执行的 Python 绝对路径。
- `NW_SERVER_DATA_ROOTS` / `NW_SERVER_RUN_INPUT_ROOTS`：可浏览和可作为运行输入的服务器目录。

共享挂载必须保持相同绝对路径。仅在提交节点存在的软件、输入或 Python 不能用于远端 Worker。分区、节点、并行样本数、CPU、内存、GPU 和每步时限由工作台配置；单步为单节点作业，不提供跨节点训练。

```bash
command -v sbatch squeue sacct scancel sinfo
sinfo -N -o '%N %P %t %c %m %G'
test -x /opt/neuroweave/envs/scientific-py311/bin/python
test -x /opt/software/mrtrix3-master/bin/tckgen
```

这些命令不提交计算。在所选计算节点也核对同样的绝对路径。配置变更不会更改已提交运行的不可变快照。

## 可选数据脚本容器

数据工作台的隔离 Python 功能需要管理员预建 Docker 镜像。标准库环境通过 `NEUROWEAVE_DATA_PYTHON_IMAGE` 配置；表格/影像环境通过 `NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS_FILE` 指定受保护的环境清单。构建与配置见 [数据 Python 环境](../data-python/README.md)。

容器配置不替代科学 Slurm Worker。未配置镜像时相应隔离脚本功能不可用；模型不能自行安装依赖或执行任意宿主机代码。

## 核对可执行能力

启动平台后，在方法详情、存储与环境及本次 Dry-run 查看真实依赖结果；`/api/health` 也包含 Runner 可用性。可执行文件存在、Python 导入成功、实际计算成功、科研验证通过是不同层次，材料中分别提供证据。

MRtrix 纤维追踪需要所选算法对应的 FOD、种子及可选 5TT / ACT 等输入，具体以该方法的条件输入槽为准。不要把输入尚未绑定或空间条件不满足写成“已验证可运行”。

高级路径基线见 [SERVER_SOFTWARE_PATHS.md](../SERVER_SOFTWARE_PATHS.md)，许可与来源见 [THIRD_PARTY_NOTICES](../../THIRD_PARTY_NOTICES/)。
