# 公物仓成套调剂的需求替代与节支认定

本项目围绕回收业务的本地资料分析。资料包含合成原始记录、字段契约和文件体检工具；`app/` 提供业务办理入口：把部门需求、可用旧设备及调剂结果转成可审核的替代方案和节支说明。合成资料没有现实居民或企业信息，不能作为真实政策、交易或认证依据。

## 环境

使用 Python 3.11。科学计算依赖版本固定在 requirements.txt。已有相同依赖环境时无需重复安装；新环境使用 `python3 -m pip install -r requirements.txt`。资料体检工具与业务应用本身仅使用标准库。

## 现有操作

在项目根目录执行 `python3 -m support.inspect_data` 查看资料表数量并核对文件摘要、字段、身份及声明的引用。执行 `python3 -m unittest discover -s tests -v` 验证资料可读、损坏文件可被发现，以及业务规则与端到端流程（导入、方案、接受、交接、认定）符合口径。

数据保存在 data/，字段含义和适用条件见 [资料契约](docs/input-contract.md)。data/manifest.json 声明每份原始文件的身份和摘要；新增资料可在独立目录保存新清单，不应覆盖用于历史解释的原始文件。

## 业务办理

业务应用入口为 `python3 -m app.cli <子命令>`，运行时状态写入 .local/（可用 `--state` 指定路径），可审核说明导出到 outputs/，均不改写 data/ 原始记录。典型办理顺序：

```bash
python3 -m app.cli import                 # 校验并导入资料，建立初始状态
python3 -m app.cli plan                   # 生成备选方案（备选之间可共享设备）
python3 -m app.cli show-plan PLN-0001     # 查看方案明细与估算
python3 -m app.cli accept PLN-0001        # 核对当前条件后接受，形成预留并出具说明
python3 -m app.cli handover-import        # 按获知时间登记资料中的交接事件
python3 -m app.cli handover --asset A005 --kind return_requested --need N4 --note "说明"
python3 -m app.cli status                 # 当前已满足需求与净节支
python3 -m app.cli statements --need N1 --all   # 查看说明（含历史版本）
python3 -m app.cli audit N1               # 需求或资产的审计视图
python3 -m app.cli export                 # 导出 Markdown 说明到 outputs/
python3 -m app.cli reset                  # 清除运行时状态，重新办理
```

口径要点：只有获准新购且实际被调剂替代的需求范围才认定节支；不拆分需求未整组到齐不予认定；共享搬运整组只计一次，分摊依据写入分摊记录；待检测与检测结果未知的资产不作承诺；接受方案时逐台核对当前可用条件，冲突明确列出，不暗中删减套件内容。节支说明按版本保存，旧版本始终可复核。

## 容器使用

`docker build -t recovery-data .` 准备锁定依赖环境。`docker run --rm recovery-data` 运行资料体检；`docker run --rm recovery-data python -m unittest discover -s tests -v` 运行格式检查与业务测试。镜像基础已固定摘要。
