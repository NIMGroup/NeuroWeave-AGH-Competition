# Pipeline 编排、预检与可靠性

Pipeline 编排使用当前方法契约、输出目录授权与注册 Runner。平台尚无多用户身份和角色体系；执行权限与模型建议分别校验。

## 当前行为

| 原问题 | 实现 |
| --- | --- |
| 文件类型过宽，变换后丢失语义 | 单向类型兼容；专用影像可进入通用影像端口，通用影像不能反向冒充标签图。mrtransform 仅在最近邻插值且不调制时保留标签/掩膜语义，参数变化后重新计算。 |
| Dry run 通过容易被理解为科研结果正确 | 有限的图像头和跨文件检查；梯度数量、DWI 体积数、5TT 通道数、几何有效性。空间对齐和内容质量明确显示待审阅；确认流程时需要承认这些检查限制。 |
| 任务入口与历史 | 任务管理统一展示对话与 Pipeline 创建的任务，支持筛选和分页运行历史；目录创建任务使用独立上下文，不关联当前对话。 |
| 编排未完成就无法保存 | 浏览器恢复副本 + SQLite 草稿；允许临时无效连线，发布时才执行完整校验。节点位置、撤销/重做、显式保存草稿。 |
| 请求断开、重试可能重复规划 | 客户端请求 ID、持久化状态和结果、同会话串行、进度、有限重试。断线不会取消已接受的规划；重启后的未完成规划显示中断，由用户明确重试。答案与成功结果在一个事务内提交。 |
| 规划邀请只能复制文字 | 邀请按钮将建议请求放入输入框，可编辑后发送；不会自动提交。 |
| 参数改变但接口预览不同步 | 输入/输出条件继续由 Method 契约计算，生成说明也只包含当前参数下的公开接口。 |
| 一个输入端口无法接多个上游文件 | many 端口支持有序多来源；逐项路径权限、类型和样本范围校验。AverageImages、fslmerge、mrcat 对应原生命令支持独立列表 argv。 |
| 用户 Pipeline 版本混杂 | 发布不可变版本；查看版本、归档/恢复。检索仅使用最新且未归档、方法版本匹配的用户版本。旧方法契约需要迁移审阅，不静默升级。 |
| 命令收录被误认为覆盖所有原生选项 | 方法详情显示尚未接入的原生选项及原因；现有 Method JSON 是受审阅的执行契约。 |
| 无关界面变化使执行证据失效 | 指纹按方法契约、所选适配器及其执行依赖计算，包含公共安全检查；不把界面样式或其他不相关文档纳入执行指纹。 |
| 大目录和多样本检查重复工作 | 单层排序快照与分页游标；每页重新验证访问权限。元数据检查缓存按文件身份失效；有界并行 preflight。 |
| 前后端/Worker 发布不同步 | API contract 版本头、健康接口 release 标识、源文件哈希比对、独立版本 Worker、保留旧静态资源和发布备份。 |

## 自动生成 Pipeline 描述

用户完成编排后点击“生成说明”才请求服务；添加步骤、连线、参数修改、保存草稿、校验与发布均不触发生成。后端先编译校验真实方法和连接，再发送注册的方法说明、受约束的数值/布尔/枚举参数、接口和连接关系。用户填写的名字、说明、自由文本参数、输入文件路径及数据集内容不发送。

界面语言决定生成中文或英文。点击生成将替换当前说明，之后仍可手动编辑；再次生成需要重新点击按钮。模型调用使用现有服务端 API 配置，最多两次尝试，总等待上限 25 秒，最多两个并发请求；成功结果按图元数据和语言缓存。API 未配置、超时或失败时显示原因，并使用真实接口生成基础说明，不阻止草稿或有效版本保存。发布保留用户已审阅的说明；修改流程后如需更新说明，由用户再次点击生成。

这是一份流程文档，不是已执行证明；系统提示禁止虚构自动配准、验证结果或科研正确性。已发布旧版本的说明不会被后台改写。

## API

- `GET /api/library/drafts`、`PUT /api/library/drafts/{id}`：草稿和修订比较。
- `POST /api/library/pipelines/describe?language=zh|en`：返回 description、source、fingerprint、warning。
- `GET /api/library/pipelines/{id}/versions`、`GET /api/library/pipelines/{id}/migration`。
- `PATCH /api/library/pipelines/{id}/versions/{version}?status=published|archived`。
- `POST /api/chat/turns`、`GET /api/chat/turns/{id}`、`POST .../{id}/retry`、`POST .../{id}/cancel`。保留 `/api/chat` 包装入口。
- `GET /api/workflow-index?offset=0&limit=30&q=...`；`GET /api/runs` 支持 offset。
- Confirm 请求使用 `acknowledge_quality`；DryRunReport 包含 quality_checks、quality_summary、quality_review_required。
- `GET /api/health` 返回 API/frontend/backend/worker release；前端发送 `X-NeuroWeave-Contract: 2`。

## 配置

模型沿用现有后端配置，不需要新增 API key；不要把密钥放入前端。服务器读取既有 systemd EnvironmentFile。

- `LOCALFLOW_SKILL_PYTHON`：安装 NiBabel 的科学 Python。服务器为 `/opt/neuroweave/envs/scientific-py311/bin/python`。
- `WORKFLOW_PREFLIGHT_WORKERS`：默认 2，最大 4；不是 Slurm 样本运行并发数。
- `NEUROWEAVE_RELEASE_REVISION`：发布标识。
- `LOCALFLOW_SLURM_WORKER_ROOT`：新发布的共享 Worker 目录；旧目录保留，避免影响已提交任务。
- 工作区和输出目录权限变量继续沿用；缓存不替代路径授权。

SQLite 分别保存 Pipeline 不可变正文、版本状态、草稿/草稿回收站、整个 Pipeline 删除修订和持久对话轮次。删除恢复及已有任务快照边界见 [发布删除](published-pipeline-deletion.md)。

## 验证方式

```powershell
$env:PYTHONPATH='bakend'
python -m pytest bakend/tests -q
python scripts/check_release.py
cd frontend
npm test
npm run build
```

Linux 原生执行验收使用 `scripts/smoke_reliability.py --output <全新临时目录> --record-evidence`。仅生成 6×6×6 合成影像，验证 AverageImages 的数值均值、fslmerge 文件顺序、三步转换到 mrcat 的真实 Pipeline 绑定、mrtransform 标签语义条件。脚本不提交 Slurm 作业，不使用用户数据。

应用检查与真实方法证据分别维护，见 [质量检查](catalog-quality.md)。服务器目标、维护锁、版本切换和数据保护见 [部署维护](server-deployment.md)。

## 边界与未完成的深度能力

- 图像头检查目前支持 NIfTI/MGZ/MGH；MIF 等格式显示未接入通用检查，不假称完成。头信息一致不能证明解剖对齐，未逐体素检验组织概率、标签有效性或完整科研 QC。
- 通用 artifact 类型不能证明每种软件都支持任意影像编码；当前五种方法的合成验证不等于全部 328 种方法已完成科学验收，旧证据不匹配会明确显示 stale。
- 撤销/重做属于当前编辑会话；草稿恢复跨浏览器，但撤销栈不会同步。跨窗口修订冲突要求重新打开比较。
- 条件接口和输出格式仍依赖已审阅的原生契约；未接入选项不能通过自由 Shell 绕过。
- 大目录第一次排序仍需枚举该层，最多缓存 200000 个直接子项、32 个快照、60 秒；后续分页复用快照，不递归扫描。
- 对话停止是协作取消：当前 API 调用结束后到安全检查点停止，不会停止已提交的 Runner 计算。
- 完整的跨方法标准空间/坐标系推断、自动科研 QC、所有命令和全部参数组合的科学验证仍需逐项补充。

`methods/**/method.json` 是当前原生契约来源；旧 bootstrap 生成脚本不能代替人工审阅，也不要无检查覆盖已完善的契约。`scripts/check_release.py` 对原生契约、allowlist、参数绑定和双语描述进行一致性校验。
