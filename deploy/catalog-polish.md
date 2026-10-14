# 分类与双语文案维护

数据集专属 Skill 用 `scope: dataset` 标识，单列在数据集适配与质控分类，例如 ABCD、ABIDE、UKB、HCP 和 SEED。Method 按实际功能分类，分类只帮助检索，不代表执行权限或软件已安装。

用途说明应准确描述现有实现、输入是文件还是目录、固定调用模式、输出和限制。不能根据名称扩大科学含义：原有名为 zALFF 的区域摘要实际使用滤波后信号 STD；原 alpha-asymmetry 函数计算 F3/F4 信号均值比值。文档完善不改写原算法。

只有完整上游统计存在时才说明选项覆盖率；否则显示已登记参数和未开放项。重新生成 FOD、复用已有 5TT 等选择规则属于原生 Skill 指导，不能用泛化关键词自动扩大处理范围。

## 双语边界

前端使用系统语言，后端按 `Accept-Language` 或 `language=zh|en` 投影显示文本。覆盖目录、用途、参数帮助、输入输出、条件提示及流程编辑返回的元数据。词典位于 `bakend/resources/catalog-locales/`。

ID、CLI 参数、默认值、枚举值、条件表达式、路径绑定、用户 Pipeline 名称/描述和对话历史保持原值。切换语言不调用模型，也不修改数据库或执行快照。原始参考和版本帮助保留原文以便核查。

## 增量维护

```bash
python scripts/catalog_translation.py export --output /path/to/translation-jobs.json
```

只有获得将公开目录元数据发送至已配置模型服务的授权后，才执行批量翻译；禁止加入研究数据、会话、密钥和私有路径。

```bash
python scripts/catalog_translation.py translate \
  --jobs /path/to/translation-jobs.json \
  --output bakend/resources/catalog-locales \
  --env-file /path/to/backend.env \
  --allow-provider
python scripts/catalog_translation.py check
python scripts/build_capability_tree.py --check
python scripts/check_release.py
```

脚本缓存完成条目、限制并发和重试，保护命令、路径及占位符。检查通过说明覆盖了当前提取的显示字段，不能代替专业术语校对。能力树增加文案后需再次导出增量。

原有重复概览、旧执行 manifest 和无调用者生成器已退出原生目录维护；仍有运行引用的文件、实现源码、版本帮助、许可证及验证报告需保留。清理精确变化以 Git 记录为准，不能依据旧清单删除服务器文件或正在使用的任务输出。
