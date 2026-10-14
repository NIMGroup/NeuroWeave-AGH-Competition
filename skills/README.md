# Skill 指导与参考资料

当前平台从 `*/skill.json` 加载原生 Skill。Skill 描述可以完成的任务、适用条件、选择规则和方法角色，通过 `method_refs` / `pipeline_refs` 引用独立定义。执行参数和输入输出以 `methods/` 为唯一来源；组合关系由 `pipelines/` 或用户保存的 Pipeline 维护。

分类包含 task（任务统筹）、tool（软件选择）和 dataset（数据集专属）。仅保留规范 Skill ID，不再解析旧版本的 Skill 别名。

`selection_rules`、`applicability`、`limitations` 和 `examples` 是可选的专属指导。只登记能由本 Skill 的实际契约或参考资料支持、且能帮助区分操作的信息；没有额外信息时省略字段。单一方法的输入、参数和输出直接读取 Method，不为填满字段复制方法详情或改写通用模板。示例应表达有实际选择差异的场景，不使用“查看某方法所需的参数”这种批量生成的占位示例。

通用查询、输入复用、科学产物区分及执行边界由 `bakend/app/skill_catalog.py` 的 `SKILL_PROMPT` 统一维护，规划阶段继续使用现有任务契约约束。读取 Skill 不会把通用句子填回其 JSON。详情 API 对省略的指导返回空列表，页面与模型概览省略空栏目。维护记录见 [专属指导整理](../deploy/skill-guidance.md)。

补充的科研背景、结果解释、使用限制和排错资料放在各 Skill 的 `references/` 内，并通过 `skill.json` 的 `references` 显式登记。例如：

```json
"references": [
  {"id": "notes", "title": "背景与注意事项", "path": "references/notes.md"}
]
```

参考资料是可选的；没有额外知识需要补充时保留空列表即可。模型通过 `read_skill_reference` 按需分页读取，省略 `reference_id` 读取首篇，并返回主题列表供后续选择；没有资料时返回 JSON 概览。默认提示词、目录列表和方法加载不会读取全部参考正文。

不再发现或读取 `SKILL.md`，也不保留旧文件名的兼容逻辑。原有文档中的子代理、Shell 委派、安装操作、固定输出路径和旧确认流程已移除；不能用参考说明替代 Method 的参数、输入输出或执行权限。登记路径必须位于本 Skill 的 `references/` 内，不能通过调用参数指定文件路径。

原生软件帮助和被 Method 引用的实现脚本保留原文件及哈希，独立于整理后的说明。原作者、来源与许可证继续保留；不把新说明冒充原始帮助或原始验证证据。

修改当前功能说明应编辑 `skill.json`；修改参数契约应编辑对应 `method.json` 并检查依赖 Pipeline。不得对原始脚本、模型类名、来源路径或哈希做产品名的全局替换。被 Method 引用的实现仍按来源和执行契约验证。

维护流程见 [原生结构](../deploy/structured-skill-methods.md)和[参考资料整理](../deploy/skill-references.md)；原始实现归属见 [来源记录](../UPSTREAM.md)。
