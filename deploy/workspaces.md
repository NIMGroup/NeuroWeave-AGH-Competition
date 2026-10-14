# 服务器输出目录管理

现在按用途分为两个独立范围：数据集文件面板提供只读访问，选择数据集后模型即可查询；输出目录用于保存结果及文件管理。数据助手另有批量修改预览，当前访问身份可以授权具体清单中的原地修改，见 [数据助手与权限](data-assistant.md)。沿用原 workspace_id 和数据库记录兼容历史对话。网页右上角和任务卡片中的“选择输出目录”打开居中的可调整大小窗口，独立的输出目录侧栏继续提供文件管理入口。

## 使用

1. 浏览允许的服务器根目录，或填写服务器绝对路径。目录仅按层读取，文件夹优先；可筛选、排序、显示隐藏项、刷新、使用面包屑和上级目录。默认一页 80 项，滚动到底自动继续加载。
2. 选择“当前目录作为输出目录”，填写名称。已有输出目录可从已登记列表定位，再确认选择；浏览或取消不会切换对话关联。若当前路径已登记，直接复用记录；其他目录先登记名称后再选择。对话保存 `workspace_id`，历史对话恢复该关联。没有输出目录的旧对话继续使用原行为。根路径不可直接修改，避免把旧对话静默关联到别的目录。登记时要求服务器后端账户对目录有读取、写入和进入权限；与任何已登记数据集重叠的目录不能登记为可管理输出目录。不会自动执行 chmod/chown，也不能替用户提升 Linux 权限。
3. 选中文件查看大小、修改时间、权限和完整路径。文本预览按段读取；影像只展示元数据，内容分析继续使用现有 Skill/Runner。路径支持悬浮、横向滚动和复制。选择窗口四边和四角可拖动调整大小；文件管理面板左边缘可拖动，聚焦后也可用左右方向键调整宽度。
4. 文件面板和助手提出的新建目录、复制、移动、重命名、删除均先显示待运行卡片，用户检查准确路径后点击运行才执行；覆盖与删除仍须明确确认。只读浏览和读取无需点击运行。复制/移动目标必须包含最终文件或文件夹名称。模型没有运行或确认工具。普通对话不提供模型写工具，但用户可以在文件面板手动管理文件。
5. 删除默认进入回收站。恢复可以使用原位置或填写另一个输出目录内目标；占用的位置不会被静默覆盖。永久删除必须显式勾选并确认。移除输出目录记录只隐藏记录，目录、文件、对话和任务输出全部保留。
6. 数据集和输出目录分别选择；网页“查看数据集文件”提供分页浏览、详情及文本预览，隐藏写操作按钮。锁定流程后不能从页面切换输出目录或数据集。新流程默认输出到 `输出目录/运行ID/步骤/`（新流程 relative_path 为 `.`），可在流程卡片中调整输出子目录；历史流程保留原输出配置。已提交运行保存根目录快照，不随之后的对话切换或流程修改迁移。

## 配置

选择阶段尚未登记输出目录时，也可在当前浏览目录中点击“新建文件夹”。确认完整路径后创建一个直接子目录，并自动进入新目录，随后可点击“当前目录作为输出目录”。创建不会自动登记父目录或切换对话关联。完成提示在 2.5 秒后自动收起，也可手动收起；错误提示保留，操作和审计记录不删除。选择阶段的 mkdir 使用 `root_path + path`，仍受允许根、符号链接、数据集只读及操作确认约束；其他写操作仍必须指定已登记的 `workspace_id`。

本地示例（PowerShell，在启动后端前设置；目录应已存在）：

```powershell
$env:WORKSPACE_ALLOWED_ROOTS='D:\data;D:\projects\NeuroWeave\workspaces'
$env:WORKSPACE_DEFAULT_ROOT='D:\projects\NeuroWeave\workspaces'
$env:WORKSPACE_PAGE_SIZE='80'
$env:WORKSPACE_TEXT_MAX_CHARS='12000'
$env:WORKSPACE_TRASH_DIR='.neuroweave-trash'
```

未配置时只允许项目的 `workspaces` 目录；开发环境会创建这个默认目录。它被 Git 和发布复制规则排除，不打包用户文件。

以下是服务器配置示例；当前两站的实际根目录及环境文件见 [服务器部署](server-deployment.md)，不要按示例覆盖现有配置：

```dotenv
WORKSPACE_ALLOWED_ROOTS=/home/data
WORKSPACE_DEFAULT_ROOT=/home/data/neuroweave/workspaces/hipeson
WORKSPACE_PAGE_SIZE=80
WORKSPACE_TEXT_MAX_CHARS=12000
WORKSPACE_TRASH_DIR=.neuroweave-trash
```

Linux 多根目录可使用 `:` 或 `;` 分隔；Windows 使用 `;`。如果管理员设置 `WORKSPACE_ALLOWED_ROOTS=/`，可以浏览后端账户有权限读取的服务器路径，仍遵守特殊目录、敏感文件和输出目录边界。代码没有默认开放 `/`。发布脚本只补充缺失的配置项，保留已有管理员设置。

Slurm 输出目录和输出目录必须在目标计算节点上以相同绝对路径挂载、可由任务账户访问。通常使用 `/home/data` 共享存储。输出目录配置写入冻结的 Slurm payload 环境白名单，控制端和 Worker 都验证输出范围。选择不共享的目录不会使它自动同步到计算节点。

回收站默认位于每个输出目录的 `.neuroweave-trash`。配置相对目录时仍位于输出目录内；配置绝对目录时按输出目录 ID 分隔，且必须处于允许根目录内。移动和回收站操作使用同文件系统原子重命名；跨文件系统移动会失败并保留源文件，可先复制，再分别确认删除。不要把不同挂载点的数据统一移到另一个文件系统上的回收站。

## API

输出目录 CRUD 的新入口是 `/api/output-directories` 和 `/api/output-directories/{id}`，回收站为 `/api/output-directories/{id}/trash`。原 `/api/workspaces` API、workspace_id 字段、WORKSPACE_* 配置以及历史工具事件保留兼容；旧记录若指向数据集或失去写权限，只可浏览，不能绕过新保护。

数据集文件入口为 `GET /api/datasets/{id}/files/list`、`inspect`、`search`、`read-text`；这些接口只读。批量改动由独立的 `/api/data-operations` 接口管理，范围、账户、清单确认和恢复均在服务端校验。数据集读取权限来自 `LOCALFLOW_DATA_ROOTS`，输出管理权限来自 `WORKSPACE_ALLOWED_ROOTS`，互不扩大。

| 方法与路径 | 用途 |
|---|---|
| `GET /api/workspaces` | 输出目录列表 |
| `POST /api/workspaces` | 登记已有目录：name、root_path、description |
| `PATCH /api/workspaces/{id}` | 修改名称、说明或 active/archived 状态 |
| `DELETE /api/workspaces/{id}` | 移除记录，保留磁盘及历史关联 |
| `GET /api/fs/roots` | 允许根目录、默认目录和分页/文本限制 |
| `GET /api/fs/list` | 一层分页列表 |
| `GET /api/fs/inspect` | 存在性、类型、大小、修改时间、权限、规范化路径 |
| `GET /api/fs/read-text` | UTF-8 文本：offset、max_chars、truncated、next_offset |
| `GET /api/fs/search` | 文件名/扩展名 glob，限定深度与结果数量 |
| `POST /api/fs/mkdir` | 创建文件夹提议 |
| `POST /api/fs/copy` | 复制提议 |
| `POST /api/fs/move` | 移动提议 |
| `POST /api/fs/rename` | 重命名提议，destination 只接受新名称 |
| `POST /api/fs/delete` | 回收站删除提议；permanent 默认为 false |
| `POST /api/fs/read-sensitive` | 单次敏感文本预览提议，仅人工网页入口 |
| `GET /api/fs/operations/{id}` | 持久化操作状态 |
| `POST /api/fs/operations/{id}/confirm` | 显式 `confirmed: true` 和匹配 conversation_id 后执行 |
| `GET /api/workspaces/{id}/trash` | 最近 500 条未恢复回收站记录 |
| `POST /api/fs/trash/{id}/restore` | 恢复提议，可另指定 destination；仍须确认 |
| `GET /api/workspace-tool-events?turn_id=...` | 当前对话工具进度 |
| `PATCH /api/conversations/{id}` | workspace_id，null 表示解除关联 |
| `PATCH /api/datasets/{id}/workspace` | 可选关联，不改变数据输入目录 |
| `PATCH /api/workflows/{id}/output` | expected_revision + 输出目录 relative_path |

列表参数：`workspace_id` 或 `root_path`、`path`（默认 `.`，可为范围内绝对路径）、`cursor`、`limit`（1–200）、`sort`（name/size/modified）、`filter`、`show_hidden`。返回 `items/has_more/next_cursor`。游标绑定目录修改状态及筛选参数，失效时需刷新。为了排序会枚举当前层，但只保留一页条目，不递归读取整棵树。

文件写请求字段：`workspace_id/path/destination/conversation_id/overwrite/permanent`。POST 返回 pending，不等于完成；确认有效期 10 分钟，单次消费。结果包括成功或失败状态，失败原因可查询。确认请求在线程池执行，浏览器断开不取消已经开始的文件操作，更不会取消 Runner。

## 模型工具

输出目录注册到模型 API 的函数为：

- `list_output_directory`
- `inspect_output_path`
- `search_output_files`
- `read_output_text`
- `create_output_directory`
- `copy_output_path`
- `move_output_path`
- `rename_output_path`
- `delete_output_path`

选择数据集后另外注册四个只读工具：`list_dataset_directory`、`inspect_dataset_path`、`search_dataset_files`、`read_dataset_text`。没有任何数据集写工具；同时选择两种目录时，模型通过工具名称区分输入与输出，不按同名相对路径猜测范围。

另有内部路由函数 `continue_workflow_planning`，把处理需求交回已有能力树、Skill 和规划系统。上述输出工具不能指定其他 workspace_id/root_path，范围从当前对话注入。数据集工具的范围来自当前 dataset_id，并经现有数据根许可重新校验。没有通用 Shell 或模型确认函数。每轮最多 8 个模型回合、16 次调用；模型目录列表默认 50 项、上限 200，搜索上限 200，文本最多 8000 字符，可继续分页或缩小检索范围。HTTP 文件面板的配置上限独立保留。

只读结果进入工具观察，按事件保存到对话时间线；文本内容按需发送给当前配置的模型提供商，因此输出目录应选择允许该模型处理的数据目录。敏感文本例外：需要独立人工确认，只在确认响应返回网页，不发送给模型，不把文本持久化到操作记录或审计。

## 权限与一致性

- `Path.resolve()` 检查允许根和输出目录边界，拒绝 `..`、越界符号链接、循环链接与非普通文件。写操作拒绝任何路径组件的符号链接；Windows 同时拒绝 reparse point。
- 即使根为 `/`，仍屏蔽 `/proc`、`/sys`、`/dev`、`/run`、`/boot` 等特殊目录；隐藏 `.env`、凭据/token、私钥、敏感系统文件。Linux 权限由后端账户本身决定，不使用 sudo。
- Linux 文件内容打开及写入使用逐层 `O_NOFOLLOW` 目录描述符，重命名使用 `renameat2(RENAME_NOREPLACE)`；不能覆盖确认后新出现的目标。复制先建立同目录暂存副本，成功后再原子替换；旧目标先进入回收站。
- 文件操作使用 Python/OS 文件接口，不拼接 Shell。影像、压缩文件、NUL/控制字节以及无法严格 UTF-8 解码的内容不能作为文本返回。搜索最多深度 5、200 个结果、20000 个扫描项、5 秒，达到限制明确返回 truncated。
- 每次实际写入记录开始/成功/失败、后端账户、会话、工具、源/目标、时间与确认状态。确认前重新检查源/目标指纹。普通对话不给模型写工具，模型生成的“完成”文本不能替代操作成功状态。
- 旧输出目录写接口和 Runner 仍不能写入已登记的数据集。原地数据修改仅允许通过独立的数据操作清单，在当前访问身份、范围校验和明确授权后执行。登记数据集会检查进行中的文件操作和活动输出，避免并发改变权限范围。
- 活动任务的数据集、已绑定输入及输出目录不能移动、改名、删除或覆盖。文件操作先保留路径，新的 Runner 不能占用同一路径；长时间复制不会持有 Runner 进度锁。
- 覆盖、删除和数据集原地确认独立于 LLM，来自不允许网页 Origin 的写请求被拒绝。配置平台账户后，按数据集/输出目录 ID 校验范围，记录账户 actor 和后端 OS 账户；会话、工具事件及后台记录也校验归属。未配置账户时保留旧访问方式，通过准确清单确认受限原地修改；选定输出目录允许非覆盖写入。平台账户不等于独立 OS 身份。

## 验证与维护

从项目根目录使用锁定开发环境：

```powershell
$env:PYTHONPATH='bakend'
$env:LOCALFLOW_SLURM_ENABLED='false'
python -m pytest bakend/tests/test_workspaces.py bakend/tests/test_dataset_output_access.py -q
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
```

Linux 回归覆盖符号链接、目录描述符和路径竞态等 Windows 无法完整验证的条件。真实模型工具验收需要当前提供商支持结构化 function calling；失败保留接口错误。纯文件回归不调用模型或提交科研任务。

服务器的实际环境文件、服务名、数据库和发布前检查见 [服务器部署](server-deployment.md)。旧 `localflow-backend.service`、`deploy/native-backend.env` 及固定目标脚本不作为当前两站的操作示例。

## 操作限制与故障恢复

- 输出目录是文件范围和任务输出配置，不替代科学软件自己的参数验证。已有数据集输入选择和历史样本绑定规则保留；输入选择器优先数据集，没有数据集时从输出目录所对应的授权数据根开始。输出目录若不属于已配置的数据输入根，不会自动扩大科学 Runner 的输入许可。
- 单次递归复制/目录修改限制 100000 项。跨文件系统 move/restore 不做隐式复制后删除。大型数据不使用 Git checkpoint。
- 进程异常中断的文件操作保留 running 记录，不会自动重放；需检查 operation、源/目标和回收站后处理。复制的暂存路径记录为 staging_path，失败时可保留部分副本，原目标不会因复制失败被先删除。回收站 prepared 记录也保留，以支持中断后的人工核查。
- Windows 使用原生文件权限及规范化/reparse 检查；Linux 服务器另有目录描述符防护。本功能不是针对可任意重命名挂载点、改变目录所有权或修改数据库的本机管理员的安全隔离。

## 维护入口

后端有界读取及目录授权位于 `bakend/app/filesystem_reader.py`、`dataset_files.py`、`workspace_paths.py`、`workspace_io.py`、`workspace_service.py` 和 `workspace_tools.py`；路由由 `bakend/app/api/` 装配，任务与文件共享的锁由应用依赖统一接线。

前端目录选择和操作确认位于 `frontend/src/features/workspace/`，输入绑定位于 `frontend/src/features/workflows/`。流程输出变化及会话关联仍通过后端修订检查；详细职责见 [代码维护](code-architecture.md)。

## 助手的当前输出写入策略

助手使用当前访问身份，在会话选定的输出目录中可以直接新建、非覆盖复制、改名和移动；覆盖与删除仍返回准确路径的确认卡片。用户要求仅预览时不会执行。普通文件操作由数据批次保留恢复记录，目录操作沿用文件面板的审计与回收站机制。数据集查询保持只读，原地修改通过独立的准确清单授权；不要求配置用户管理。具体边界见 data-assistant.md。

文件清单、整理方案和只读搜索默认跳过符号链接（Windows 同时跳过 reparse point），返回 `skipped_symlinks`、最多 50 条 `symlink_examples`、样例截断标记和 `warnings`。`complete_scan` 仅代表遍历完成；`scope_complete` 说明是否有排除，数量不包含未跟随的链接目标。只读 inventory/search 可显式设置 `follow_links=true`，仅允许当前所选数据集或输出目录内的目标，并检查每一跳、拒绝敏感/内部路径、循环和重复目标；链接查询不会扩大修改范围。文件改动仍拒绝经过链接，必须基于真实相对路径准备预览并手动运行，覆盖和删除另行确认。脚本容器输入仍执行原有严格树校验，不自动扩大挂载权限。
