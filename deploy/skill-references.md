# Skill 按需参考资料

平台从 skill.json 加载规范 Skill，参考 Markdown 通过显式登记按需读取。参考资料补充背景和结果解释，参数与执行契约由 Method 和 Runner 维护。

## 内容范围

资料用于解释科研背景、方法前提、质量检查、数据集特点及结果限制。与当前契约重复或描述退役执行架构的文字不继续维护；无需补充知识的 Skill 使用 JSON 概览。

DWI 登记的 7 份 MRtrix3 3.0.8 原生帮助保留原字节和来源哈希。整理后的 notes 与原始帮助、实现脚本及执行证据分别管理，不将新说明标记为原始帮助或科学验证。

原作者、许可和来源保留在各资料及仓库归属记录中。历史来源清单描述导入快照，不要求退役文件仍存在。已移除独立说明的 beautiful-log、claw-shell、skill-updater、harness-core 原作者为 chengwang96，原标注 MIT License；相应归属记录继续保留，来源见 [UPSTREAM](../UPSTREAM.md)。

## 登记与读取

`skill.json` 的 `references` 是包含 `id`、`title`、`path` 的列表。ID 在本 Skill 内唯一，路径必须是 `references/` 下的 Markdown；无补充资料时使用空列表。

`read_skill_reference(skill_id, reference_id?, offset?, max_chars?)` 不接受文件路径。省略 ID 读取首篇，结果包含可选主题列表；明确 ID 时仅读取相应资料。没有登记资料时返回 JSON 概览。默认 6000 字符、最多 12000 字符，按 `next_offset` 继续阅读同一主题。

HTTP 接口仍为 `/api/skills/{id}/reference`，使用 `reference_id` 选择主题。未登记 ID 返回 404，参数超出声明范围返回 422，读取时路径越界或文件丢失返回 400。Skill 详情仅返回主题元数据，避免目录加载时读取全文。

目录加载验证登记字段，发布检查验证文件存在与目录边界；读取时再次检查解析后的路径，阻止符号链接跳出当前 Skill 的参考目录。没有 Markdown 不妨碍 Skill 加载，已登记但丢失的文件应修正登记，不能静默当作正常参考。

## 维护

在 JSON 维护当前适用范围、选择规则和方法引用。在 Methods 维护准确参数与端口，在 Pipelines 维护步骤连接。参考资料保留独有知识，删除与契约重复或冲突的操作说明；新工具有必要时补充原理、前提、质量检查及来源，不复制整套软件手册。

修改原生帮助或实现时仍需要审阅相应 Method 和执行证据；整理参考文案不代表所有科学流程已在真实数据上验证。
