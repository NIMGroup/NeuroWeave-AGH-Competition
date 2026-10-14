# FreeSurfer autorecon2 验证误报恢复

FreeSurfer 7.4.1 的 `autorecon2` 启用 `DoWhitePreAparc`，
`autorecon3` 才启用 `DoWhiteSurfs`。依据：
[官方 recon-all 源码](https://github.com/freesurfer/freesurfer/blob/v7.4.1/scripts/recon-all)。
Runner 现在分别验证各阶段的非空体数据、双侧表面和统计文件；
autorecon2 接受每侧的 `white.preaparc` 或 `white`，不要求下一阶段的统计。

## 恢复范围

仅处理已经最终确认且 Dry-run 通过的三阶段配方，状态必须为
`autorecon1=succeeded / autorecon2=failed / autorecon3=queued`，并且
错误必须是旧验证器缺少 `surf/lh.white` 的误报。
恢复会检查同一工作区内的产物、`recon-all.done` 的阶段和受试者路径、
完成证据时间、日志末尾成功标记、无 `IsRunning*` 标记以及续跑环境。
不满足条件返回 HTTP 409，不修改运行记录。

## 操作

部署前通过生产数据库只读查询检查全部 `queued/running/cancel_requested` 任务，
同时检查遗留 recon-all 进程。没有活动任务时才重启原生后端加载修复。

调用 `POST /api/runs/{run_id}/recover-freesurfer?dry_run=true`，
请求体为 `{"expected_revision": <原运行快照版本>}`。省略 `dry_run` 也只做核验。
核验返回原 SUBJECTS_DIR、通过验证的文件和下一阶段参数。

确认续跑后，使用相同请求体调用 `?dry_run=false`。
服务保留原 run_id、原始快照、autorecon1 结果和历史错误事件，
新增 `freesurfer_stage_recovered` 审计事件并登记 autorecon2 制品，
然后只调度 autorecon3。重复提交会被拒绝。
恢复期间不会重建数据库，也不会删除现有输出。

通过 `GET /api/runs/{run_id}` 和 `/events` 观察结果。
此入口是针对验证误报的受限维护接口，不是通用重试或任意命令执行接口。
