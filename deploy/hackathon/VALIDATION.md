# 开发与交付检查

服务器使用说明见 [README.md](README.md)，科学软件配置见 [SOFTWARE.md](SOFTWARE.md)。以下检查在开发环境或隔离候选环境执行；不要为了材料整理启动真实计算、重置已有会话或清理科研数据。

## 本地检查

后端使用 Python 3.11 和锁定依赖；前端使用 Node 24。以下命令从仓库根目录执行：

```bash
python -m venv bakend/.venv
bakend/.venv/bin/python -m pip install --no-deps -r bakend/requirements-dev.lock
PYTHONPATH=bakend LOCALFLOW_SLURM_ENABLED=false bakend/.venv/bin/python -m pytest bakend/tests -q --tb=short
bakend/.venv/bin/python scripts/check_release.py
bakend/.venv/bin/python scripts/build_capability_tree.py --check
node --test scripts/hackathon/server-plugin-tests.mjs
(cd frontend && npm ci && npm run lint && npm test && npm run build:agh && VITE_AGH_SERVER=true npm run build)
```

Windows 使用 `bakend/.venv/Scripts/python.exe` 和对应 PowerShell 环境变量。测试使用独立临时数据库和合成输入；不能用单元测试输出声称真实 AGH、Slurm 或科研验收完成。

工具契约来自现有业务模型。变更时在锁定 API 环境运行 `PYTHONPATH=bakend python scripts/hackathon/build-server-contracts.py`，审阅产生的 JSON，不覆盖人工完善的方法定义。

## 依赖与当前部署检查

安装与准备脚本的 Bash 语法可通过 `bash -n scripts/hackathon/<script>.sh` 检查。固定 AGH Web 源文件会在应用适配前核对哈希，不允许补丁落在共享运行时软链接上。

准备正式实例后：

```bash
source scripts/hackathon/server-env.sh
tsx scripts/hackathon/verify-profile.mjs
curl --fail http://127.0.0.1:8127/api/health
```

`verify-profile` 核对实际插件、工具、客户端模块、宿主能力限制和官方包状态，不发送模型需求、不提交科学计算。服务是否可用以实际环境和本次 Dry-run 为准。

## 真实证据

需要演示或验收时，由操作者在 AGH 提出任务，在工作台选择输入、参数和资源并手动运行；保存原生工具轨迹、AGH 服务动作与 Slurm Run 的真实关联。提交回执、实际成功和科学验证分别记录。

当前结果验证由同一 AGH 会话按需发起。成功后要求模型读取真实 run_id、调用验证工具并解释范围；错误时要求读取实际错误，后续重试仍通过工作台按钮。

旧六工具合成演示、旧材料生成器与旧审批入口已从本分支移除。历史内容可在 Git 历史追溯，不用于证明当前服务器版已经通过验收。

## 打包

提交改动后运行 [源码打包器](../../scripts/package_release.py)。打包器拒绝未提交的跟踪改动，只读取该提交的 Git archive，输出 revision.txt 及 SHA-256 索引。参赛源码包中的材料链接与运行记录仍需队伍自行整理，见 [提交说明](SUBMISSION.md)。
