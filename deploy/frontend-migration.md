# 前端发布工具与适用边界

当前前端从 `frontend/` 构建，开发与检查见 [前端指南](../frontend/README.md)。现有服务器使用 nginx 挂载各站 `frontend/dist`，API 代理到对应 Unix socket；真实目标、服务和版本核对见 [服务器部署](server-deployment.md)。

## 构建与静态资源核对

从项目根目录运行 `npm --prefix frontend run lint`、`npm --prefix frontend test` 和 `npm --prefix frontend run build`。构建产物必须对应已审核源码，并保留文件清单与 SHA-256。发布先安装带哈希资源，再切换入口；保留旧资源以供已打开页面继续加载。

检查实际静态入口、资源 HTTP 返回及 API 契约，而不只检查健康接口里的 frontend_revision。该标识由后端进程环境提供，单独更新前端时可能不同于实际静态资源。

## 仓库中的发布工具

`scripts/package_release.py` 按提交生成源码包并排除环境文件、数据库及用户输入输出。`scripts/deploy_frontend_release.py` 支持源码/预构建校验、服务器并发改动检查、备份和回退，但其生产目录、Compose 与网关假设属于旧部署拓扑。

此脚本将本文列入同步文件清单，文件继续保留用于解释工具边界。现有 3000/3002 使用独立 checkout、runtime 环境文件和目录挂载，不能直接套用脚本内固定的 `/home/hipeson/LocalFlow` 目标或旧网关操作。使用前须审阅目标及切换/恢复步骤；历史固定发布命令不作为当前操作说明。

前端发布与后端/Worker 发布分别核对。后端、数据库或 Worker 未更新时，不应以新前端提交号宣称整站升级。涉及后端契约变化时同时验证兼容性，并按目标站的维护锁、活跃任务及数据保留规则安排发布。

恢复应使用对应发布清单，确认未覆盖后续有效变化；恢复静态入口不回滚数据库，也不停止科研作业。
