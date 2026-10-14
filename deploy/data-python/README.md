# 数据脚本固定依赖环境

这是管理员维护的 Linux amd64、Python 3.11 镜像构建入口。模型和运行中的后端不会调用构建脚本，不允许联网安装包。

| 环境 | 预装主要依赖 |
| --- | --- |
| stdlib | 既有 Python 标准库镜像 |
| table | NumPy 2.4.6、pandas 3.0.6、openpyxl 3.1.5 |
| imaging | table 全部依赖及 NiBabel 5.4.2 |

table.lock 和 imaging.lock 锁定全部依赖的版本及审核 wheel 的 SHA-256；wheels.json 记录官方 PyPI 下载来源。Dockerfile 固定基础镜像摘要。更新版本时必须重新解析兼容 wheel、审阅哈希、构建并运行验收，不能仅改模型提示词或使用浮动镜像标签。

使用服务器现有 Python 3.11 运行以下维护命令。pip download 仅下载 wheel，不向该 Python 环境安装依赖；构建过程离线安装并执行 pip check，然后核对容器中的实际版本。

```sh
python scripts/build_data_python_images.py --docker /usr/bin/docker --output /protected/runtime/data-python-environments.json
```

构建结果为 table/imaging 对应的不可变 image ID、实际依赖版本、锁文件哈希和基础镜像摘要。将结果存于服务器受保护目录，仅配置目标后端的 NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS_FILE。标准库镜像仍由 NEUROWEAVE_DATA_PYTHON_IMAGE 指定。不要提交服务器运行配置或密钥到 Git。

GET /api/data-environments 返回实际启用环境及固定配额。脚本请求 environment 选择 stdlib/table/imaging，resource_profile 选择 small/standard/extended；未知、未启用的环境直接拒绝。第三方库环境仍执行输入只读、无网络、非 root、只读根目录、资源配额和输出归档检查。输出覆盖和删除权限不会因为选用库而改变。

运行 bakend/tests/test_data_libraries.py 时设置 NEUROWEAVE_DATA_PYTHON_ENVIRONMENTS_FILE 指向构建结果，才执行真实 Docker 验收。默认 CI 使用合成脚本和文件验证后台状态、取消、授权和存储，不自动下载镜像。
