# git-essentials：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

## 提交、引用与协作

提交记录文件快照及父子关系，分支和标签是对提交的引用。修改工作区、暂存内容和创建提交属于不同状态，检查差异时需要明确比较对象。

历史重写会改变提交标识，可能影响其他分支和协作者。恢复引用依赖本地历史及对象是否仍被保留，不能把删除分支等同于立刻删除全部提交，也不能认为历史对象永久存在。

合并冲突解决后需要检查最终行为；消除冲突标记仅说明文本可以继续处理。参考说明不授予仓库修改或远端推送能力。

## Reference

Official docs: https://git-scm.com/doc  
Pro Git book: https://git-scm.com/book  
Visual Git guide: https://marklodato.github.io/visual-git-guide/

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明更新时间：2026-03-25 23:57 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
