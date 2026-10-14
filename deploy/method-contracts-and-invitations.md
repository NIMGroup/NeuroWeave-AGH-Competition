# Method 输出语义及对话规划邀请

执行与输出语义以 `methods/<group>/<id>/method.json` 为唯一契约源。Skill 负责选择与统筹，Pipeline 保存方法版本及具体组合。

## 输出语义

- 参数和端口明确区分脑区分区、扩散梯度、组织标签、追踪分布、张量标量、响应函数及变换矩阵。通用图像、报告或目录不能据名称推断专用科学含义。
- `freesurfer.recon_all` 提供受试者目录及 `t1w`、`brain_t1w`、`parcellation`、`aseg`、`aseg_stats` 文件端口。其中 `parcellation` 对应 `mri/aparc+aseg.mgz`，只在 `all` / `autorecon3` 声明和登记。`brainmask.mgz` 是脑提取后的强度图，不声明为二值掩膜。
- Runner 从运行快照中的端口登记实际存在且非空的文件。旧运行快照不被追加新文件要求；早期阶段不会登记目录中遗留的后期分区文件。FreeSurfer 文件附带结构像空间及“空间对齐未验证”元数据。
- Reviewed Runner 从 Method 快照取输出类型，避免文档与执行收集器各维护一份类型导致漂移。已修改的静态 Pipeline 同步引用新版本并暴露适用的文件端口；用户保存的 Pipeline 不会被静默改写。
- `labelconvert` 明确接收与产生节点分区，负责标签编号/LUT 转换。它不配准图像；`tck2connectome` 需要分区和纤维处于相同物理坐标空间。FAST 组织类别、Nilearn 聚类标签、FreeSurfer 解剖分区不会被自动当作同一产物。
- Skill 专属指导尊重本轮目标与输入复用约束，方法能力通过当前 Method 的说明和端口确认。
- 失败建议的上下文交错保留各次检索的高排名候选，防止未排序的语义扫描结果挤掉先前已找到的 FreeSurfer 等方法。分页语义检索提示词明确实际 JSON 字段数量及长度限制。

## 对话行为

`response_policy.offer_workflow` 表示“可提供规划邀请”，不等于 `action=plan`，也不授权执行。语义分类仍决定本轮是解释、比较、查询、规划或修改流程。

对于有具体处理目标、目前只在讨论方案的 `answer` / `explore`，模型先回答，再提供一句可复制发送的请求。请求需包含本轮讨论的目标、所选路线、已知输入和复用约束；不得把示例中的 FreeSurfer 或其他条件生搬到无关问题。普通聊天模式下会提示切换任务模式。问候、纯概念定义、目录列表、未决目标以及明确不需要后续建议的情况不附加邀请。

示例（仅当符合当前讨论）：

> 如果你需要按上述方案生成可审阅的流程，请发送：“请使用现有 T1w，通过 FreeSurfer 生成解剖脑区分区；复用已有纤维结果，不重新进行追踪。”

用户随后发送请求才进入规划校验。实际运行仍需输入绑定、参数审阅、dry-run 和网页执行确认。模型措辞仍有随机性；离线测试验证路由和 Prompt 接入，不等于真实模型每次都按预期表达。

## 验证与维护

在项目根目录执行：

```powershell
$env:PYTHONPATH='bakend'
python -m pytest bakend/tests -q
python scripts/audit_method_contracts.py --output method-contract-audit.json
python scripts/catalog_translation.py check
cd frontend
npm test
npm run build
```

审计脚本只读元数据，不扫描数据集、不调用外部模型、不运行科学软件。它检查输入输出汇总与端口一致、输出路径、说明和 Reviewed 类型一致性，并列出仍为通用类型的端口供后续审查。检查通过不代表全部命令经过真实数据验证。

FreeSurfer 依据：[输出文件清单](https://surfer.nmr.mgh.harvard.edu/fswiki/ReconAllOutputFiles)、[recon-all 阶段](https://surfer.nmr.mgh.harvard.edu/fswiki/recon-all)。MRtrix 依据：[labelconvert 使用说明](https://mrtrix.readthedocs.io/en/3.0.8/quantitative_structural_connectivity/labelconvert_tutorial.html)。

## 边界

- 声明 FreeSurfer 文件端口不表示完成了当前科研数据的重建或空间配准验证；实际证据与输入质量分别审阅。
- 通用图像变换尚不能在所有参数组合下自动继承任意专用科学类型。无法通过真实端口组成的空间转换链应明确返回契约缺口，不能伪装成完整可执行流程。
- 旧历史运行只有目录端口时不会自动补登新文件；可以从目录中选择已存在的文件。旧用户 Pipeline 遇到版本不匹配需重新检查后保存。
- 契约或执行代码变化后，既有运行验证证据可显示为过期；更新契约不会生成新的科学验证记录。
- 部署版本、Worker 和数据保留规则见 [服务器部署](server-deployment.md)，不能使用旧单次发布范围决定当前变更。
