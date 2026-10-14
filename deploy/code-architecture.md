# 模块边界与维护指南

本文描述当前仓库的模块职责。目录定义、编排、文件授权和执行分别维护；拆分模块时通过 API 与 Prompt 回归检查防止意外改变外部行为。

## 后端职责

| 模块 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| `bakend/app/main.py` | 暴露 `app`，保持 `uvicorn app.main:app` 启动方式 | 初始化细节和业务逻辑 |
| `settings.py`、`bootstrap.py`、`application_services.py` | 配置、服务依赖装配、共享锁与路径保护接线 | 请求处理或模型规划 |
| `application.py`、`api/` | FastAPI 生命周期、按资源分类的路由、HTTP 错误转换 | 保存另一份领域校验规则 |
| `chat_coordinator.py` | 恢复可信上下文、安排对话/工具/规划调用 | 提交计算任务 |
| `chat_service.py`、`llm/` | 模型传输；需求解析、能力路由、方法规划、记忆摘要与有限重试 | 文件写入和 Runner 执行 |
| `agent_runtime.py`、`agent_state.py` | 确定工具准备方案、持续目标和完成条件、方案核对和真实产物完成判定 | 将模型理解作为运行授权 |
| `agent_team.py` | 主控、按需功能 Agent、职责工具隔离及缺口分发 | 自动提交计算或文件改动 |
| `agent_reports.py`、`agent_claims.py`、`script_contract.py` | 逐项观测与推断、原始字段核对、明确缺口、脚本输出契约及静态核验 | 用概括或计数相等证明全量语义正确 |
| `agent_artifacts.py`、`script_verification.py` | 当前产物与实际阶段注入、定向只读结果访问；手动沙箱函数案例、生成清单的不变量核验 | 自动运行脚本或以历史消息判断当前执行状态 |
| `agent_facts.py`、`agent_compression.py` | 一套带来源的共享事实、完整证据读取、过程摘要 | 用模型摘要覆盖真实事实或授予权限 |
| `agent_identity.py`、`agent_query_pages.py`、`agent_metadata.py` | 等价只读查询标识、分页对齐和固定批量概况 | 任意代码执行或修改源文件 |
| `assistant_loop.py`、`assistant_runtime.py` | 通用工具循环、证据进展、预算、上下文预览与中断报告 | 启动文件操作、脚本或计算 |
| `assistant_inventory.py`、`filesystem_reader.py`、`directory_snapshots.py` | 完整选择的只读统计、同轮查询缓存、可续读的模型分页和未变化目录的游标恢复 | 将查询快照当作执行清单、扩大权限或授权操作 |
| `assistant_tasks.py`、`workspace_tools.py` | 服务端任务记录、状态刷新、完成核验；工具暴露、范围校验与分发 | 把历史检查点或模型消息作为运行授权 |
| `assistant_contract.py` | 区分持续目标与本轮步骤，核对处理来源、模式、动作、阶段和选择范围 | 代替用户授权或保证自然语言理解总是正确 |
| `assistant_memory.py` | 保存用户原话、带来源的工作笔记、条件变更和历史证据引用；按需检索原始消息和旧证据 | 把模型推测记成工具事实、跨范围继承证据或代替运行授权 |
| `assistant_step_tools.py` | 查询后补齐步骤，准备提议前校验完整契约 | 扩大既定范围、继承已完成操作或启动文件修改 |
| `data_selection.py`、`data_transform.py` | 绑定完整文件子集与已完成批次来源，生成批量字面名称变换清单 | 自动复制旧任务或运行文件改动 |
| `workflow_service.py` | 流程生命周期、乐观修订、审核状态与提交保存 | 完整检索、建图或文件映射实现 |
| `workflows/contracts.py`、`inputs.py`、`warnings.py` | 确定性的参数、端口、输入解析与提示计算 | 模型请求、数据库写入 |
| `workflows/catalog.py`、`selector.py` | 方法选项投影、能力提示与顺序 | 自动执行用户尚未确认的任务 |
| `workflows/state.py`、`session.py` | 派生界面状态；共享修订锁和持久化边界 | 任意 Shell |
| `workflows/routing.py`、`planning.py` | 需求路由、按真实输入输出构建并校验流程 | 在生成说明文字时绕过图校验 |
| `workflows/bindings.py`、`preflight.py` | 当前/历史样本输入绑定；Dry-run 证据 | 启动作业 |
| `pipeline_models.py`、`pipeline_compiler.py` | 请求结构；纯内存编译注册方法组成的图 | 依赖完整应用或临时伪造服务对象 |
| `pipeline_repository.py`、`pipeline_management.py` | 不可变发布版本；草稿、归档和迁移审阅 | 从用户字符串拼接软件命令 |
| `pipeline_library.py` | 目录检索与普通 Workflow 集成 | 通过继承隐式共享存储属性 |
| `local_store.py`、`store_schema.py` | 持久化接口；可重复执行的增量建表/迁移 | 清理研究数据和运行输出 |
| `execution/` | 编译确认后的执行契约、权限检查、Runner、样本队列及 Slurm | 把模型回答直接作为命令执行 |

依赖从应用装配指向业务组件，再指向模型、纯计算或持久化接口。`llm` 仅通过 `ModelGateway` 调用模型；`workflows` 不反向导入 `WorkflowService`；Pipeline 编译器不导入数据库或 API。组件构造参数显式声明所需依赖，不使用 `__getattr__` 转发或拆文件式的业务 mixin。

跨模块写操作使用公开边界：`LocalStore.transaction()` 保证一次读、检查、写入的事务，`RunService.mutation_lock` 与 `WorkflowService.mutation_lock` 协调提交与文件操作。共享锁只能在应用装配处统一连接，不应在路由里新建另一个锁。关闭 Web 监控不等于取消 Slurm 作业。

`PipelineRepository.get()` 用一次联表查询读取不可变版本、该版本的发布状态和全 Pipeline 的删除修订号。创建任务检查返回版本自身的状态，避免再次扫描全部版本。恢复后保留删除记录并递增修订号，防止旧窗口重新匹配删除前的修订号；删除不会修改已经保存在任务中的执行快照。

## 前端职责

数据助手通过 data_tools.py 提供模型工具，data_operations.py 管理确定清单、授权、进度及恢复，data_auth.py 在模型调用之外校验平台账户和目录 ID。生成 Python 仅由 data_sandbox.py 在受限容器运行，结果进入同一预览提交链路。data_routes.py 的确认/恢复接口不作为模型工具暴露。前端 features/data/ 负责登录与批量确认卡片，助手是否交给科学规划器由明确工具调用决定。

- `App.tsx` 保留当前对话和数据集/输出目录的协调；`ConversationHistoryPanel` 负责历史列表渲染和翻页观察。
- `useMessageViewport` 管理消息容器滚动及前插历史时的锚点恢复，向界面提供操作函数，不暴露内部跟随标记。
- `PipelineEditor` 组织编排操作；`PipelineMethodPalette` 只展示和选择注册方法，`PipelineNodeInspector` 根据实际契约展示条件输入与参数。
- `LibraryPanel` 只保存待查看条目的 ID、类型和版本，定义加载交给查询缓存；版本选择只改变 ID/版本，不先请求再由 Effect 重复请求。Method 详情、方法面板和画布共用 `libraryQueries.methodQuery`；关闭窗口或切换条目后，查询可以取消失去观察者的请求。
- `libraryQueries.refreshPipelineViews` 统一刷新发布、归档、删除及恢复影响的目录、回收站和对应 Pipeline 的详情、版本、来源及迁移缓存。更新这类操作时保持该列表完整，不分别在按钮组件中复制失效规则。
- `pipelineGraph.ts` 保留连线、共享输入和拓扑顺序的规则；`PipelineCanvas` 负责画布交互，不能自行重写一份类型或条件规则。
- `usePipelineDraft` 负责持久草稿、浏览器恢复、撤销/重做；`usePipelineDescription` 只在用户点击生成按钮时请求说明，负责取消过期请求和保护手动编辑；保存、校验与发布不生成说明。
- 各文件的注释解释数据所有权、并发边界、失效规则和异常恢复，避免为明显的赋值逐行写重复说明。

## 新增功能应该改哪里

1. 新增方法或条件参数：修改 `methods/` 中的真实契约；必要时修改受信任执行适配器。复用条件计算，不在 Prompt 或前端硬编码第三份规则。
2. 修改需求理解或回答策略：助手模式修改 `agent_team.py` 的主控和职责提示及 `agent_state.py` 的持久理解；旧受限入口修改对应 `llm/` 策略和验证器；模型 HTTP 行为放在 `ChatService`。错误仍须保留低置信度及有限重试。
3. 修改流程规划：在 `workflows/routing.py` 或 `planning.py` 中处理；审核、锁定和修订提交放在流程服务，不由规划器直接保存。
4. 扩展 Pipeline：数据结构放在 `pipeline_models.py`，图规则放在编译器，草稿/版本放在各自存储组件；发布前仍校验注册方法和真实接口。
5. 新增 API：添加到相应资源路由并通过依赖容器访问服务，不重新往 `main.py` 添加端点。
6. 扩展界面：把纯规则、异步状态和展示组件分开；一个组件不要同时负责画布、模型调用、历史恢复及列表加载。

## 验证与 CI

使用 `bakend/requirements-dev.lock` 安装完整锁定依赖，并运行 `pip check`、后端完整回归和 `scripts/check_release.py`。前端使用 `npm ci` 后执行 lint、测试和构建。可直接运行的命令见 [项目说明](../README.md)。CI 在 Ubuntu 24.04 / Python 3.11 / Node 24 下执行，Windows 上因符号链接和 POSIX 权限跳过的测试仍由 Linux CI 验证。

`test_module_boundaries.py` 检查依赖方向、API 路径/状态/响应类型、Prompt 内容，以及输出目录修改后的修订与会话关联。Prompt 指纹防止迁移代码时意外改变指令；有意修改 Prompt 时先审阅语义并补充行为测试，再更新快照。

FastAPI 0.141.1 的包含路由以延迟展开结构保存。路由契约测试使用框架的 `iter_route_contexts` 遍历有效路由，同时核查 OpenAPI 和真实 HTTP 响应；旧开发环境的平铺 APIRoute 也可检查。不能只筛选顶层 `app.routes`，否则会把已注册接口误报为缺失。新增回归覆盖嵌套前缀、状态码、响应模型及重复注册，未跳过失败断言或降低锁定版本。

验证结果以具体提交的 CI 或本次运行记录为准，不在维护指南中固定旧测试数量。Windows 的符号链接/POSIX 条件回归需同时由 Linux 验证；依赖弃用和构建大小提示单独评估。

测试使用临时数据库、合成文件和进程内 ASGI 客户端，不启动常驻服务或提交科研任务。通过应用测试不表示每条科学软件命令均完成真实数据验证。服务器发布另按版本差异及活跃任务状态处理，GitHub 推送不自动替换服务器部署。
