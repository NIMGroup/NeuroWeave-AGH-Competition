# 删除用户已发布的 Pipeline

用户 Pipeline 列表和详情页均提供“删除 Pipeline”，确认窗口说明作用范围。删除以整个 Pipeline 为单位，包含全部发布版本；在“已删除 Pipeline”中可以恢复。任务创建选择器不提供删除操作，内置 Pipeline 继续只读。

SQLite 的 `pipeline_deletions`，记录整个 Pipeline 的删除状态及递增修订号。版本正文、各版本的发布/归档状态、草稿、已有任务及执行快照不删除。删除后目录、规划绑定与新任务创建排除整个 ID，不回退到旧版本。已有任务仍按不可变执行快照展开。

删除请求比较最新发布版本及删除状态修订号，防止其他窗口刚发布的版本被误删；恢复比较删除修订号。恢复保留原有版本号和发布/归档状态，继续递增删除状态修订号，避免旧窗口的删除或恢复请求覆盖后续操作。已删除的 Pipeline 拒绝更新发布、状态编辑和新任务创建。

- `DELETE /api/library/pipelines/{id}`：`expected_version`、`expected_deletion_revision`。
- `GET /api/library/pipelines/trash`：已删除条目、版本数量、删除修订号。
- `POST /api/library/pipelines/{id}/restore`：`expected_deletion_revision`。

当前回收站查询使用 `deleted_at IS NOT NULL`；恢复后保留修订记录，因此表行数不能直接当作当前已删除 Pipeline 数量。表结构采用幂等初始化。回归覆盖全部版本隐藏、恢复状态保留、进程重建后的持久化、并发冲突、旧发布窗口、内置定义保护，以及已有任务执行配方不变。
