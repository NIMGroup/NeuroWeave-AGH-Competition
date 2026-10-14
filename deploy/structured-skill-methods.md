# Skill、Method 与 Pipeline 的原生结构

当前平台采用独立方法契约；旧 Skill 别名、workflow/recipes manifest 和 Markdown 依赖图兼容接口已移除。能力顺序来自真实输入输出依赖。与旧分支混用数据库、前后端或 Worker 前必须审核格式和版本；新部署应先使用隔离数据库与输出目录验证。

| 对象 | 定义与责任 |
| --- | --- |
| Method | `methods/<group>/<id>/method.json`；最小执行单位，唯一拥有参数、输入输出、条件、版本和执行适配器 |
| Skill | `skills/<id>/skill.json`；功能范围、方法角色、选择规则和限制，通过 ID 引用 Method/Pipeline |
| Pipeline | `pipelines/<id>/pipeline.json` 或数据库中的用户版本；步骤、方法版本、参数映射、端口连接和整体接口 |

最小单位是一条接入的软件命令、库操作或不可再拆的原有脚本，不要求把其内部实现拆成函数，也不新建算法。单命令预设可以保存为单步骤 Pipeline。同一方法被多个 Skill 引用时，不复制参数和端口。

Skill 的 `scope` 区分 task、tool 和 dataset；待接入能力单列为 `planning_capabilities`。补充资料放在 `references/` 并通过 Skill JSON 显式登记，只按需读取，不定义执行策略。特殊 `SKILL.md` 已移除。见 [参考资料边界](../skills/README.md)。

## 调用与检索

需求契约 → 能力分类与全库方法召回 → 按需读取真实契约 → 构建并校验方法图 → 用户选择、绑定输入和调整参数 → Dry-run → 确认 → 注册 Runner → 本地或 Slurm。

能力树只提供导航和排序提示，不将一个树分支作为方法搜索边界。候选不足时有界扩展检索；失败时保留诊断，不兜底推荐无关方法。详见 [检索链路](library-upgrade.md)。

Skill 内 `method_id` 表示任务语境中的候选项，`method_ref` 指向全局方法 ID；投影接口使用 `definition_id` 表示该全局定义。执行与 Pipeline 引用以独立 Method 定义为准。

例如 `mrtrix.tckgen` 只生成流线，`mrtrix.command.tcksift2` 单独接收流线和 FOD 生成权重。ACT 可以绑定已有 5TT；用户要求重建 FOD 不自动要求重建 5TT。

## 读取接口

- `GET /api/library`：Skill / Method / Pipeline 分层目录。
- `GET /api/skills/{skill}`、`GET /api/skills/{skill}/methods`：Skill 指导与分页候选。
- `GET /api/methods/{method_id}`、`GET /api/pipelines/{pipeline_id}`：全局契约。
- `GET /api/skills/{skill}/reference`：按 `reference_id` 选择已登记主题并分页读取；没有参考资料时返回 JSON 概览。
- 模型只读工具：`list_skill_methods`、`read_skill_method`、`read_skill_pipeline`、`read_skill_reference`；读取本身不提交任务。

用户 Pipeline 的草稿、校验、说明及版本 API 见 [资源库使用](native-library.md)。

## 修改方法或 Skill

1. 核对软件版本、官方帮助或已有源码，确定真实入口和限制。
2. 直接维护 `method.json` 的用途、参数说明、默认值、选项、输入输出及条件；执行语义变化时更新版本，并审阅依赖 Pipeline 的 `method_version`。
3. 为新入口登记受信任 Runner 和固定 argv/API 映射。发现可执行文件或增加文档不授予运行权限。
4. 在相应 `skill.json` 中引用方法，并用 role 说明用途；不在 Skill 维护第二套参数定义。组合过程使用 Pipeline 的显式连接。
5. 更新能力树及双语显示文本，运行契约/条件/编译回归。执行逻辑变更后重新做隔离软件验证并记录证据。

```bash
python scripts/build_capability_tree.py
python scripts/catalog_translation.py check
python scripts/audit_method_documentation.py --output /path/to/new/report.json
python scripts/check_release.py
PYTHONPATH=bakend python -m pytest bakend/tests -q --tb=short
```

使用锁定的开发依赖，安装和前端检查见 [项目说明](../README.md)。早期 `build_mrtrix_commands.py` / `build_scientific_commands.py` 仅保留初始导入规格，不能无审阅覆盖已人工完善的原生契约；已删除的旧 Skill 分层生成脚本不再是维护步骤。

## 能力边界

当前注册 328 个 Method、105 个规范 Skill、24 个内置 Pipeline。画布、用户保存、持久草稿、动态接口、共享输入及有序多来源已实现；使用方式见 [资源库](native-library.md)。软件命令及全部参数组合仍未穷尽接入，`option_coverage` / 未开放选项用于说明边界。

文档完整性、软件就绪、当前执行证据和真实数据适用性分别判断。不要把某次历史探测的就绪数量当成当前服务器结果。发布包必须包含原生定义和所引用实现，旧任务使用的 Worker 目录保留至任务结束。
