# 平台运行说明

适用仓库：`NIMGroup/NeuroWeave-AGH-Competition`，默认分支 `main`。本说明从空的 Linux x86_64 环境安装当前服务器比赛版；已有部署只按需要更新，准备插件会停止并重启该实例的 AGH daemon，维护前应确认没有活动会话或计算。

## 环境与目录

| 项目 | 要求 |
| --- | --- |
| 系统 | Linux x86_64，Bash、Git、curl、tar、xz、rg、sha256sum、SSH |
| 服务工具 | Node.js 24.21.0、pnpm 10.34.5、Python 3.11.15、uv 0.11.28 |
| AGH | 官方固定提交，具体版本见 [运行锁](../../integrations/agh/lock.json) |
| 计算（仅实际执行需要） | 已配置的 Slurm，提交账户可用 sbatch / squeue / sacct / scancel / sinfo |
| 软件与数据（仅实际执行需要） | [执行软件配置](SOFTWARE.md)；计算节点能读取相同的输入、Worker、软件、Python 与输出绝对路径 |
| 模型 | AGH 官方配置中的 Agnes 服务；API Key 由部署者本地配置 |

依赖安装在 `runtime/hackathon/`；业务状态在 `runtime/server/`。

## 仅规划快速启动：无需计算环境

可以调用 Agnes，但尚未安装科学软件或连接 Slurm 时，使用仅规划配置。先取得源码并按第 2 节准备 uv，然后在仓库根目录执行：

```bash
bash scripts/hackathon/install.sh
mkdir -p runtime/server
cp deploy/hackathon/planning.env.example runtime/server/deployment.env
bash scripts/hackathon/prepare-server.sh
```

之后按第 5 节启动三个 AGH 服务并配置 Agnes。无需设置 Worker、科学 Python、节点、真实数据集或输出目录，即可发送需求、检索方法、生成和编辑草稿以及创建 Pipeline。新安装未配置 Worker 时也默认采用仅规划模式；显式配置文件更便于复现。

仅规划保留真实方法契约，但不声称软件已安装。工作台显示仅规划状态，真实运行及文件执行操作被后端拒绝；未选择的数据与未验证的条件继续标为待补齐。

准备实际执行时，改用 deployment.env.example，将 NW_SERVER_MODE 设置为 execution，并按下文发布独立 Worker、配置科学软件、数据及 Slurm。切换模式前确认实例没有活动任务，重启三个服务使配置生效。也可用 NW_SERVER_STATE 指向项目 runtime 下的独立状态目录，分别保存规划与执行实例。

## 1. 获取固定版本

```bash
git clone --branch main https://github.com/NIMGroup/NeuroWeave-AGH-Competition.git
cd NeuroWeave-AGH-Competition
git rev-parse HEAD
```

复现实验时使用与视频、记录对应的提交。源码归档内也有 `revision.txt`；解压后的源码可运行，下载官方 AGH 依赖仍需要 Git。

## 2. 安装服务依赖

先按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)准备 uv 0.11.28，然后在仓库根目录执行：

```bash
bash scripts/hackathon/install.sh
```

安装器校验 Node 下载哈希、安装固定 Python 和 API 锁定依赖，下载并构建固定 AGH，应用受校验的 Web 适配，并安装前端依赖。它不安装科学软件、不启动服务、不配置密钥、不提交 Slurm 作业。环境要求见 [软件配置](SOFTWARE.md)。

## 3. 发布独立 Worker 并配置路径（仅实际执行需要）

从 Git checkout 导出同一提交到所有计算节点可见的共享路径。下面的共享根只是示例，须替换为本集群目录：

```bash
revision=$(git rev-parse HEAD)
worker=/home/data/neuroweave/worker/releases/agh-agnes/$revision
test ! -e "$worker"
mkdir -p "$worker"
git archive HEAD bakend/app bakend/resources skills methods pipelines models | tar -x -C "$worker"
printf '%s\n' "$revision" > "$worker/revision.txt"
mkdir -p runtime/server
cp deploy/hackathon/deployment.env.example runtime/server/deployment.env
cp deploy/hackathon/science-paths.env.example runtime/server/science-paths.env
chmod 600 runtime/server/deployment.env runtime/server/science-paths.env
```

编辑这两份副本：

- `deployment.env`：填写独立 Worker 路径、输入范围、专用输出根和科学 Python。
- `science-paths.env`：按已安装软件填写路径与许可证位置，详细解释见 [执行软件配置](SOFTWARE.md)。
- `runtime/server` 必须是该实例独享目录；不可让第二个后端进程使用同一数据库。
- 没有 Git 的归档复现，将归档中的 Worker 所需目录复制到一个新的共享目录，并复制 `revision.txt`；不要复用可变的其他站点 Worker。

配置中的 `NEUROWEAVE_RELEASE_REVISION` 应填写本次源码的完整提交号。源码包不会携带服务器状态、模型 Key 或科研数据。

## 4. 准备工作台

```bash
bash scripts/hackathon/prepare-server.sh
```

准备器在实际执行模式校验 Worker 和科学 Python；两种模式均创建专用状态及输出目录、生成私有 Bridge 凭据、构建官方 SDK 和嵌入工作台，再通过官方 PackageManager 安装及信任插件。准备结束时关闭维护 daemon，之后使用下面独立启动命令管理进程。

## 5. 启动并配置 Agnes

分别在三个终端从仓库根目录启动；先 daemon，后 Web：

```bash
bash scripts/hackathon/server-backend.sh
bash scripts/hackathon/server-daemon.sh
bash scripts/hackathon/server-web.sh
```

| 进程 | 默认监听 |
| --- | --- |
| FastAPI 私有 Bridge | 127.0.0.1:8127，单 Uvicorn worker |
| AGH daemon 的 WebSocket | 127.0.0.1:46173 |
| 官方 AGH Web | 127.0.0.1:4187 |

打开 AGH Web 的设置页，通过官方连接测试和保存流程配置 Agnes。也可复制 [模型配置模板](agnes.env.example)到 `runtime/server/agnes.env`，本地填写并设为 600 权限后运行：

```bash
chmod 600 runtime/server/agnes.env
bash scripts/hackathon/configure-agnes.sh
```

配置脚本仅接受模板列出的 Agnes 地址，通过官方 `config.test/config.save` 写入私有 secret store。实际模型必须出现在连接测试返回的模型列表中。比赛版不读取旧 ChatService 的模型 Key；不要把填好的配置提交 Git。

## 6. 从自己的电脑访问

```bash
ssh -N -L 4187:127.0.0.1:4187 -L 46173:127.0.0.1:46173 user@server
```

浏览器访问 `http://127.0.0.1:4187`。保持 SSH 连接；关闭转发后，本机页面及 WebSocket 会断开。AGH 的 Host / Origin 校验保留，使用对应回环地址访问。

新会话先选择 AGH 工作区，再点击输入区的 **NeuroWeave 工作台**，设置数据集与输出目录，然后发送需求。仅规划可以跳过数据集与输出选择，直接描述目标。AGH 工作区是会话运行目录；业务数据集可来自账户有权限的其他服务器目录，二者独立。

接入计算环境后，在“流程与运行”选择输入与参数，Dry-run 后选择分区和节点，点击运行。执行固定为 Slurm / 按样本运行；CPU、内存、GPU、并行样本数、每步时限和目录结构仍可配置。预检和真实执行中的硬性检查继续生效，额外审批弹窗已移除。

工作台持续显示任务状态、Slurm Job ID、日志和产物；刷新或重新打开后读取已有记录。需要模型解释与核验时，在同一会话提供运行 ID 并要求 AGH 查询结果。当前版本不自动在计算完成时发起模型验证。

## 7. 可选 3004 静态入口

4187 是完整 AGH 页面；3004 是引导页面，支持部署状态检查。使用默认端口时：

```bash
source scripts/hackathon/server-env.sh
(cd frontend && VITE_AGH_SERVER=true npm run build)
```

将 `frontend/dist/` 放到 Nginx 的静态目录，并按 [Nginx 模板](nginx.conf.example)修改监听地址、静态根目录和 Bridge 端口。它仅代理健康状态、概览与短期产物下载，完整业务请求通过 AGH BFF；不要把 Bridge 整体开放出去。

远程电脑还需添加 `-L 3004:127.0.0.1:3004`；如 Nginx 绑定服务器特定网卡，转发目标改为其真实监听地址。3004 内的 AGH 链接仍指向本机转发的 4187。

生产可以为三个启动脚本各建一个 systemd user unit，`WorkingDirectory` 指向本提交目录，`ExecStart` 使用 `/usr/bin/bash /absolute/project/scripts/hackathon/server-组件.sh`，Web 在 daemon 就绪后启动。已有实例的单位名与部署路径由维护者提供，本仓库不自动改写现有服务或清理历史任务。

## 状态核对与常见问题

```bash
curl --fail http://127.0.0.1:8127/api/health
source scripts/hackathon/server-env.sh
tsx scripts/hackathon/verify-profile.mjs
```

- AGH 无法打开：核对 daemon / Web、SSH 转发、4187 与 46173，以及实际 Host / Origin。
- 没有可用节点：用当前提交账户检查 Slurm 分区和节点状态，再核对 Worker / Python / 数据共享路径。
- 方法不可执行：检查该方法详情的依赖、软件绝对入口、许可证与科学 Python；安装包可导入不等于所有方法适配完成。
- 已选参数仍不能运行：检查必填输入、输出根、节点资源与本次 Dry-run 的具体阻碍。
- 提交回执丢失：查询原动作和运行，保持现有监控；未知提交不能重复提交。
- 页面显示成功：继续核对真实产物与方法质量报告，文件完整性不能代替科研有效性。

开发检查与交付见 [VALIDATION.md](VALIDATION.md)，材料准备见 [SUBMISSION.md](SUBMISSION.md)。
