# 原生资源目录、检索与命令扩展

当前平台以 Method 为执行单元、Skill 为统筹指导、Pipeline 为可复用的真实方法连接。当前静态目录包含 105 个规范 Skill、328 个 Method、24 个内置 Pipeline；用户保存的 Pipeline 另从数据库动态加载。

## 查找与规划

1. 需求归一化生成 TaskContract，分别描述目标、允许使用的已有输入、禁止复用的结果、是否允许生成中间结果和任务范围。最多 8 条、每条 300 字的语义检索改写仅用于召回，不扩大执行授权。
2. 能力树提供功能分类提示，不把某个树分支当成搜索边界。独立召回精确命令名/Method ID、任务语义改写的稀疏匹配、实际输出类型的上游依赖，以及相关 Pipeline 的真实成员和连接。
3. 首轮最多提供 32 个方法、上溯 3 层。候选不匹配或连接校验失败时，分批检索全库简短元数据（每页 100 条，最多 800 条），扩展到 80 个方法、5 层。只向模型提供命中的完整参数契约。
4. 模型仅返回已注册 Method 的 DAG。平台检查 ID、参数、条件输入输出、连接类型、拓扑、外部输入范围、最终目标和无关步骤；校验后才生成流程卡片。
5. 模型失败会保留诊断并有限重试；未找到合格图时返回候选不匹配，不推荐第一个方法。诊断包含实际扫描范围、截断和失败页面，因此有预算限制或模型失败时不能声称“平台不存在该方法”。

树由 `scripts/build_capability_tree.py` 从原生引用生成，保留能力 ID 供任务范围限制使用；叶节点显式列出 `skill_ids`、`method_ids`、`pipeline_ids`。功能目录与执行权限、执行可用性独立。新增资源后运行生成器，CI 可用 `--check` 检查漂移。

## 分层浏览与语言

`GET /api/library` 支持 `category`（稳定功能路径）、`software`、`language=zh|en`；返回全量匹配集合的分类/软件 facets，再分页返回 items。计数不局限于当前页。三个列表均按处理目标浏览，支持软件筛选、搜索和详情；Skill 标题继续使用其名称。

前端 API 携带 `Accept-Language`，资源查询缓存区分语言。后端对目录元数据做显示投影，保留 ID、CLI 参数、条件、默认值、绑定、路径和用户编辑内容；对话内容不翻译。语言切换不调用模型。词典位于 `bakend/resources/catalog-locales/{en,zh}.json`，库界面固定文字位于 `frontend/src/lib/library-en.json`。

数据集专属分类和双语目录的维护方式见 [目录整理与双语维护](catalog-polish.md)。语言切换使用离线词典；原始软件帮助仍保留原文。

## 真实命令与扩展

已登记的 FSL、ANTs、FreeSurfer 和 MRtrix3 命令覆盖影像运算、统计、GLM/置换检验、裁剪/合并/拆分、变换组合、去噪、偏置校正、组织分割、标签统计、表面、Fixel、流线和轨迹标量等。

这些命令最初从受审阅规格导入，之后已在 `methods/**/method.json` 中完善语义与条件；该 JSON 是当前唯一契约，不应重新运行 bootstrap 生成器覆盖它。源帮助位于 `deploy/software-contracts/scientific/` 和 MRtrix3 帮助目录。新增入口仍需同步受信任 Runner 登记；用 `scripts/check_release.py` 检查契约与执行登记，新增文档本身不授予执行能力。

Runner 使用 Python subprocess 的 argv，不通过 Shell。输入分别授权，输出绑定到当前运行目录；文本值只允许枚举或登记的数值语法。FSL 长参数遵循 `--flag=value`，Fixel 输出文件遵循原命令的相对目录约定。互斥、必填及可见条件复用现有前端与后端条件引擎。可选多值选项不能只填一部分。目录输入不递归跟随未知目录/越界链接。

环境使用既有 `LOCALFLOW_FSL_ROOT`、`LOCALFLOW_ANTS_ROOT`、`LOCALFLOW_FREESURFER_HOME`、`LOCALFLOW_MRTRIX_ROOT`。已审核服务器版本为 MRtrix3 3.0.8、ANTs 2.6.5、FreeSurfer 7.4.1 和当前 FSL 安装；MRtrix 运行前核对版本，其他三类尚未实现严格的运行时版本比较，换版本后应重做验证。

这不是把 `/opt` 每个可执行文件全部包装成方法。环境管理、交互程序、任意脚本入口、未审核的选项以及需要更复杂数据契约的命令仍不自动开放。详情列出 `option_coverage` 说明未开放项；实际开放参数以 Method 定义为准。一个合成用例也不等于测试了命令的全部可选参数组合。


## 维护与验证

新增方法后核对契约、Runner 登记、能力树及显示翻译，运行 `scripts/check_release.py`、`scripts/build_capability_tree.py --check` 和相应回归。当前匹配执行证据由注册表计算，不以历史批次通过数量判断可用性。

官方帮助保存在 `deploy/software-contracts/`，合成执行报告和按方法绑定的证据保存在 `deploy/method-verification/`；版本、参数与执行依赖应匹配。详见 [质量与证据](catalog-quality.md)。

服务器发布目标、现有服务名及版本核对见 [服务器部署](server-deployment.md)。旧发布目录和固定 Worker 路径不用于当前操作。
