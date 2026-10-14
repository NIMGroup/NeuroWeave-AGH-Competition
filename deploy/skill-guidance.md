# Skill 专属指导维护

`skill.json` 的 `selection_rules`、`applicability`、`limitations` 和 `examples` 只保存本 Skill 特有的适用情况、选择依据、限制与实质性示例。这些字段可省略，不为非空校验复制通用模板；省略不代表待补全。

指导必须由现有 Method/Pipeline 契约或参考资料支持。例如 ADNI 当前执行入口是目录巡检与 DICOM 转换，不能暗示尚未接入的专属字段处理；MNE ICA 支持 EOG/ECG 关联成分检测及明确排除索引，限制应与实现一致。

共享查询、输入复用和执行政策由 `bakend/app/skill_catalog.py` 的 `SKILL_PROMPT` 维护。已选 Skill 向模型提供职责与实际存在的专属指导；API 对省略字段返回空列表，详情和模型概览省略空栏目。

维护时检查文本列表类型、非空文本、重复项和跨 Skill 的重复话语。单方法参数直接查 Method，参考背景通过登记主题按需读取，不在指导里复制整套契约。修改职责、引用或限制后运行原生定义与相关加载/API 回归；修改 Prompt 时另做行为测试，不能只更新快照。

格式见 [Skill 指导](../skills/README.md)，按需资料见 [参考资料](skill-references.md)，新增执行能力见 [原生结构](structured-skill-methods.md)。
