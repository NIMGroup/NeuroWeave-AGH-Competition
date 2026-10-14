# Pipeline 方法参数与转换预设

详情画布和草稿编辑器共用 `PipelineMethodParameters`。节点选择后显示相同的下拉框或输入框、全部可选值及说明、方法默认值、取值范围和参数条件。只读详情额外显示 Pipeline 预设值、固定参数标记，并展示当前条件下不生效的参数以便检查完整契约。草稿继续按原有条件显示和编辑参数。显式 `null`、`false`、`0` 不会被方法默认值覆盖。

内置 Pipeline 仍然只读；查看不保存草稿、不修改参数、不启动任务。通过“基于此 Pipeline 创建”生成独立副本后才能编辑。Pipeline 固定值来源于原始步骤的 `fixed_parameters`，方法默认值来源于 Method 契约，两者不会混为一谈。

## dcm2niix 预设

当前保留三条单步骤 Pipeline，均调用同一 `dcm2niix.convert` Method。默认转换设置相同：GZip、生成 JSON、sidecar 匿名化、文件名 `%p_%s`；差异在固定参数及开放给任务的参数约束。

| 标识 | 职责 | 固定参数 | 可配置参数 |
| --- | --- | --- | --- |
| `reviewed-dcm2niix-convert-y-aaf8accb64` | GZip 转换 | `compression=y` | JSON sidecar、sidecar 匿名化、文件名模板 |
| `reviewed-dcm2niix-convert-y-true-989e0368fb` | 固定生成 JSON | `compression=y, bids_sidecar=true` | sidecar 匿名化、文件名模板 |
| `reviewed-dcm2niix-convert-y-true-4ca43bff7b` | 固定匿名化 sidecar | `compression=y, anonymize=true` | JSON sidecar、文件名模板 |

三条不是不同的转换实现，也尚未合并为唯一通用 Pipeline。序列维度由 dcm2niix 识别；固定生成 JSON 不代表完整 BIDS 整理，sidecar 匿名化不代表图像去面部处理。

已退役的重复 ID 不提供别名解析。既有任务仍使用创建时的不可变执行快照，不随目录标签和描述变化更新。修改或删除预设前检查当前方法引用及有效数据，不使用历史测试条目数量判断可以删除的数据。
