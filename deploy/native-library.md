# 资源库与 Pipeline 使用

技能侧栏包含 Skill 和 Method，Pipeline 使用独立侧栏；目录支持功能分类、软件筛选、搜索、分页和显式详情入口。Skill 另区分任务、工具和数据集专属；可以查看详情、加入当前会话、移除单项或清空已选项。Method 详情展示真实参数、条件输入输出、环境状态和执行证据。

内置定义来自仓库；用户 Pipeline 及草稿保存在当前部署的 SQLite 中。保存流程不修改安装环境，也不把用户路径写入内置定义。

内置和已发布 Pipeline 通过只读画布查看，可选择节点检查参数和连线，查看不会保存草稿或修改定义。点击“基于此 Pipeline 创建”生成独立编辑副本，使用新草稿恢复身份和新发布 ID。

## 创建和复用

1. 在 Pipeline 页面打开编辑器，将 Method 拖到画布或点击添加。点击方法查看详情，选中节点后调整该步骤参数。
2. 从输出端口连接到输入端口，或依次点击端口。两个输入端口可建立共享输入组，表示复用同一个文件，不增加执行依赖。多文件端口支持有序多来源。
3. 拖动节点标题布局；用平移、缩放、适应画布和自动排列检查连接。参数改变后按真实 Method 条件更新输入和输出。
4. 校验面板展示错误、警告与推导接口。未绑定的步骤输入成为公共输入；未被后续步骤消费的输出作为整体输出。关闭条件输出仍被连接时，保留连线并阻止发布，要求修正参数或连接。
5. 编排未完成可以保存草稿。完成后校验并发布不可变版本；旧版本保留，方法版本变化需迁移审阅。撤销/重做只属于当前编辑会话。
6. 如需说明，点击“生成说明”；模型不可用时显示原因并提供基于真实接口的基础说明。添加、连线、保存和校验不自动调用模型。

图必须无环，运行顺序由拓扑决定，不依赖节点在画面上的位置。共享输入兼容性、文件数量、格式与条件必需性由契约校验；不能通过自由文本连线绕过。

已经运行或审核过的普通流程可“保存为可复用 Pipeline”，导出实际执行步骤及参数，不保留样本的绝对文件绑定。再次使用 Pipeline 时重新选择输入与输出，创建待审核 Workflow。目录创建入口要求先选择输出目录，数据集可暂不选；任务上下文独立于当前对话，创建本身不提交计算。详见 [任务管理](task-management.md)。

三组织响应连接示例：Dhollander 产生白质、灰质和脑脊液响应，可分别连到 MSMT-CSD。改用单组织 Tournier 时，灰质/脑脊液连接不再有效，必须调整。系统不以“条件输出”为由一律禁止内部连接。

## 主要 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/library?kind=method&language=zh` | 分类、筛选、搜索与分页；kind 也支持 skill / pipeline |
| GET | `/api/library/methods/{id}` | Method 详情、环境和证据 |
| GET | `/api/library/pipelines/{id}?version=1` | 当前或指定 Pipeline 版本 |
| POST | `/api/library/pipelines/validate` | 校验并推导整体接口 |
| POST | `/api/library/pipelines/describe?language=zh` | 按明确请求生成说明 |
| GET / PUT | `/api/library/drafts` / `/api/library/drafts/{id}` | 列出或保存草稿 |
| POST / PUT | `/api/library/pipelines` / `/api/library/pipelines/{id}` | 发布新流程或新版本 |
| GET | `/api/library/pipelines/{id}/source?version=1` | 只读画布源数据 |
| GET | `/api/library/pipelines/{id}/versions` | 版本列表 |
| GET | `/api/library/pipelines/{id}/migration` | 方法版本迁移审阅 |
| PATCH | `/api/library/pipelines/{id}/versions/{version}` | 发布/归档状态 |
| POST | `/api/library/pipelines/{id}/workflows` | 创建待审核 Workflow |
| POST | `/api/library/from-workflow/{workflow_id}` | 按修订导出已选流程 |

草稿可移入草稿回收站并恢复；用户已发布 Pipeline 可删除整个 ID 的全部版本并恢复，保留已有任务快照和各版本归档状态。内置定义保持只读。接口与并发检查见 [发布删除](published-pipeline-deletion.md)。

修改接口检查版本/修订，避免覆盖另一窗口的更新；同源检查、维护模式、输入授权和 Runner 白名单继续生效。

完整行为边界见 [可靠性与编排](reliability-and-pipeline-authoring.md)，画布布局见 [画布说明](pipeline-canvas-ui.md)，契约扩展见 [原生结构](structured-skill-methods.md)。真实软件测试记录与当前就绪状态分别查看，不把历史发布截图或方法数量当作全部命令已经验证。
