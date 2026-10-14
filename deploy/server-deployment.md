# 服务器部署与维护

本文记录现有部署拓扑和核对方法。路径及分支于 **2026-10-12（Asia/Shanghai）** 核实；发布提交以目标站健康接口、Git HEAD 和前端版本文件共同核对，不依据文档快照覆盖服务器文件。

## 服务拓扑

服务器为 `mgr`，访问地址 `192.10.84.6`，账户 `hipeson`，本机 SSH 别名为 `localflow-server`。

| 项目 | 主站 | 开发副本 |
| --- | --- | --- |
| 地址 | `http://192.10.84.6:3000` | `http://192.10.84.6:3002` |
| 源码 | `/home/hipeson/NeuroWeave` | `/home/hipeson/NeuroWeave-task-management` |
| 分支 | `main` | `codex/model-capabilities-permissions` |
| 发布提交 | 核对 `/api/health` 的 release 与 Git HEAD | 核对 `/api/health` 的 release 与 Git HEAD |
| systemd 用户服务 | `neuroweave-backend.service` | `neuroweave-task-management-backend.service` |
| nginx 容器 | `neuroweave-frontend` | `neuroweave-task-management-frontend` |
| 发布环境文件 | `runtime/main.env` | `runtime/branch.env` |
| 数据库 | `runtime/localflow.db` | `runtime/localflow.db` |
| Worker | `/home/data/neuroweave/worker/<该站提交>` | `/home/data/neuroweave/worker/<该站提交>` |
| 默认输出根 | `/home/data/neuroweave/testing/methods-20260922/results` | `/home/data/neuroweave/testing/task-management-20261005/results` |

表中 runtime 路径分别相对于各站源码目录。新增 **3003** 为多 Agent 实验部署：源码 `/home/hipeson/NeuroWeave-multiagent`，分支 `codex/multi-agent-shared-facts`，后端服务 `neuroweave-multiagent-backend.service`，前端容器 `neuroweave-multiagent-frontend`。配置在 `runtime/app.env`，数据库为该站 `runtime/localflow.db`，默认输出为 `/home/data/neuroweave/testing/multiagent-20261012/results`，Worker 为 `/home/data/neuroweave/worker/<该站提交>`。

三站独立保存对话、任务、草稿及用户 Pipeline，不进行数据库同步。Git 推送不等于任一开发站已部署，不能据端口判断运行的是最新提交。3001 及旧 `methods-webtest` 路径不作为当前发布目标。

三站后端复用 `/home/hipeson/NeuroWeave/.venv/bin/python`（Python 3.11）。科学与 Slurm Python 为 `/opt/neuroweave/envs/scientific-py311/bin/python`。静态 nginx 挂载各站 `frontend/dist`，将 `/api/` 转发到各自 `runtime/socket/backend.sock`；端口仅绑定 `192.10.84.6`，不能用 `127.0.0.1:3000` 的拒绝连接推断站点故障。

`runtime/model.env`、`native.env`、`selected.env` 及 3003 的 `app.env` 保存对应配置；`release.json`、`deployment-state.json` 保存版本及发布结果。核实各站健康接口为 `status=ok`、`mode=model`，前端/后端/worker 标识一致，维护状态为 false。3002/3003 保持 Thinking 开启，模型标识以各站健康接口为准。不要打印环境文件全文或将密钥加入文档。

## 权限与集群边界

当前部署沿用允许根目录 `/` 的既有访问配置。可访问范围仍受后端账户权限、敏感/特殊目录屏蔽、只读数据集和文件操作确认约束，代码默认值并不开放 `/`。3002/3003 默认沿用 local 访问身份，未启用用户管理；只读查询可直接进行，其余任务须点击运行，覆盖、删除和数据集原地修改另行确认。可选平台账户不构成完整多租户隔离，具体边界见 [数据助手](data-assistant.md) 和 [目录管理](workspaces.md)。

Slurm 21.08.5 的 `all` 分区包含 mgr 和 node01–03；计算节点各登记 `gpu:1`。持久记账使用 `accounting_storage/slurmdbd` 与 `jobacct_gather/linux`，当前 `TaskPlugin=task/none`、`ProctrackType=proctrack/linuxproc`，没有 cgroup 资源强制隔离。

集群存在应用之外的 ADNI FreeSurfer 作业。应用 runs 为零不代表集群空闲；检查 `squeue` 后，用 `scontrol show job <id>` 核对脚本、工作目录和节点。应用发布不应停止这些作业或删除其代码与结果。

## 只读核对

在服务器执行：

```bash
git -C /home/hipeson/NeuroWeave status --short
git -C /home/hipeson/NeuroWeave rev-parse HEAD
git -C /home/hipeson/NeuroWeave-task-management status --short
git -C /home/hipeson/NeuroWeave-task-management rev-parse HEAD
systemctl --user is-active neuroweave-backend.service neuroweave-task-management-backend.service
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
curl -fsS 'http://192.10.84.6:3000/api/health?include_runtime=false'
curl -fsS 'http://192.10.84.6:3002/api/health?include_runtime=false'
git -C /home/hipeson/NeuroWeave-multiagent status --short
git -C /home/hipeson/NeuroWeave-multiagent rev-parse HEAD
systemctl --user is-active neuroweave-multiagent-backend.service
curl -fsS 'http://192.10.84.6:3003/api/health?include_runtime=false'
squeue -u hipeson
sinfo -N
```

同时检查本地分支、未提交改动、`runtime/release-maintenance.lock`、活跃 runs / operations / chat_turns。健康接口的版本来自进程配置；前端发布还要核对实际入口、资源哈希和发布记录。

## 发布与恢复

开发发布应指定独立分支、端口、数据库和输出目录。推送代码不改变线上部署。主站更新前须：

1. 核对目标站及源码差异、依赖和测试结果，生成带提交与校验和的候选包。
2. 保留有效环境配置与数据，建立新的固定共享 Worker 目录；维护锁阻止新写入后，再检查目标站活跃执行、规划、检查及文件操作。
3. 在允许的维护窗口切换目标后端和静态入口，核对资源哈希、接口、前后端/worker 标识及数据完整性。旧哈希资源、Worker 和已保存结果继续保留。
4. 失败时按当次发布清单恢复源码、配置和静态入口；恢复数据库前单独检查期间的新写入，不用旧快照覆盖有效数据。

仓库中的 `deploy_slurm_release.py`、`deploy_frontend_release.py` 等旧发布脚本包含 `/home/hipeson/LocalFlow` 等固定目标，缓存中的单次脚本还可能固定旧 SHA。使用前必须审阅并调整目标，不能直接当作上述两站的通用发布命令。

主站备份定时器 `neuroweave-backup.timer` 指向当前主站数据库；范围与恢复方法见 [运行与备份维护](reliability-upgrade.md)。未核实开发副本有自动备份，不应假定主站定时器同时覆盖 3002。

## 模型连接排查

先核对模型配置、当前 DNS 路由及 `resolvectl query api.deepseek.com`，再按需验证 HTTPS。DeepSeek 使用 CDN 别名 `api.deepseek.com.eo.dnse1.com`；公共域名与集群内网解析需要同时保留。现有 DNS 修复已完成，勿重跑历史修复脚本或将全服务器默认 DNS 改成公共 DNS。
