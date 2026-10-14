# 目录质量与执行证据

Skill 分为任务、软件工具和数据集专属三类。规范 Skill 描述用途和方法角色，并按需要补充专属适用条件、选择规则及限制。旧别名已移除，只有规范 ID 可查询。Method JSON 是参数、条件、端口及执行适配器的唯一契约。

注册检查拒绝非法默认值、枚举、范围、条件引用和重复 ID。`scripts/check_release.py` 另检查方法与 Runner 登记一致、执行参数绑定、非空参数/端口说明、Skill 引用、Pipeline 编译、显示翻译及依赖锁。

## 分开判断四种状态

| 状态 | 能说明什么 |
| --- | --- |
| 契约有效 | 定义满足结构和参数规则 |
| 文档完整 | 已登记字段有说明，不代表覆盖软件全部选项 |
| 环境就绪 | 当前目标环境存在所需入口和依赖，仍需检查输入和资源 |
| 执行证据 | 特定版本和环境下运行过记录中的用例，不证明所有参数组合或科研正确性 |

Method 详情的 `verification` 单独返回证据状态。`not_recorded` 表示没有匹配记录，不等于执行失败；`stale` 表示定义或执行依赖已变化；无效或越界报告不能记为通过。

记录位于 `deploy/method-verification/<method-id>.json`，绑定方法版本、执行指纹、测试类型、环境、结果、时间及报告 SHA-256。指纹按方法执行契约、选中适配器和相关依赖计算，包含公共安全检查；依赖选择见 `bakend/app/execution_dependencies.py`。不会因为无关界面文案修改而自动获得或丢失科学验证。

只有受信任维护工具写入证据，浏览器和模型不能将方法标成已验证。原始软件帮助、历史依赖探测和未绑定报告不自动升级为当前执行证据。

## 维护检查

```bash
python scripts/build_capability_tree.py
python scripts/catalog_translation.py check
python scripts/audit_method_documentation.py --output /path/to/new/report.json
python scripts/check_release.py
PYTHONPATH=bakend python -m pytest bakend/tests -q --tb=short
```

从项目根目录使用锁定开发环境运行。不要重新生成整套 Method 覆盖手工完善的语义；契约变化需审阅依赖 Pipeline，执行行为变化需有意义的 Runner 回归。

MRtrix 隔离合成测试示例（需要对应软件环境）：

```bash
/opt/neuroweave/envs/scientific-py311/bin/python scripts/smoke_mrtrix_commands.py \
  --work-dir /an/explicit/new/empty/directory --record-evidence
```

只能使用明确指定的新测试目录，审阅报告后再提交证据。`reports/` 中的 MRtrix、scientific、catalog-flags 和 reliability 报告各自限定测试版本及范围；当前有多少条匹配证据应由注册表计算，不在指南中固定一个历史数量。
