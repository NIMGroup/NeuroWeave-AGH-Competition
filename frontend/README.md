# AGH 嵌入工作台前端

当前比赛分支用 React 19 / TypeScript / Vite 构建 NeuroWeave 工作台，AGH 提供官方会话、对话和设置界面。

## 构建

从本目录运行，使用 Node 24 和 package-lock.json：

```bash
npm ci
npm run lint
npm test
npm run build:agh
VITE_AGH_SERVER=true npm run build
```

`build:agh` 将嵌入客户端构建至 integrations/agh/extensions/main/client，交由官方 AGH 客户端模块加载。设置 `VITE_AGH_SERVER=true` 的普通构建生成 3004 静态引导入口；完整工作台仍在 AGH Web 中。

未设置比赛变量的 App 与 8010 开发代理保留，供共享业务组件的隔离开发和回归使用。比赛实例通过官方 BFF 访问业务服务，不使用普通聊天页面的模型循环。

## 主要模块

| 模块 | 用途 |
| --- | --- |
| features/agh | 宿主服务桥接、工作台、管理入口、3004 引导页 |
| features/library | Skill / Method / Pipeline 浏览与画布 |
| features/workflows、features/tasks | 目标、流程参数、输入、预检与运行配置 |
| features/runs | 实际运行记录、状态增量与日志监控 |
| features/datasets、features/workspace | 服务器数据集、输出目录与文件操作 |

工作台使用 AGH 宿主 React，在 Shadow DOM 内隔离样式。新会话可在第一条消息前打开工作台；参数和执行不追加审批，运行按钮发起对应冻结动作。

当前没有对话右侧方案面板。工作区与会话管理在 AGH 侧栏，目标与流程在工作台查看。实时监控不依赖模型回复；运行完成后的模型查询与验证由同一会话按需发起。

完整运行步骤见 [平台运行说明](../deploy/hackathon/README.md)，软件接入见 [执行软件配置](../deploy/hackathon/SOFTWARE.md)，架构见 [AGH 执行链](../deploy/hackathon/ARCHITECTURE.md)。
