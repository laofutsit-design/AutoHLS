# 天枢智构 AutoHLS

面向 PYNQ-Z2 / AMD Zynq-7000 的受限 HLS 设计空间搜索、验证和交付系统。这个目录包含当前项目的完整实现源代码、测试、网页、硬件包装和审计工具，供竞赛评审或代码审阅使用。

## 快速运行

使用 Python 3.10 或更高版本，在本目录执行：

```text
python server.py
```

浏览器打开 `http://127.0.0.1:8000`。核心服务使用 Python 标准库，不需要安装 Web 框架。Windows 也可使用 `run.ps1`。默认仅在回环地址监听。

```text
python -m autohls --help
python -m autohls explore examples/matmul.cpp --goal balanced
python -c "from autohls.channel_replay import replay; print(replay('matched')['after'])"
```

工作台的公式估算仅演示流程。`research` 入口执行独立的真实验证引擎；原生验证需要 GCC、Clang 或已配置的 MSVC。具有合法 Vivado HLS / Vitis HLS 环境时，可调用真实 C 仿真、综合及 RTL 协同仿真。不会自动安装 AMD 工具或下载模型。

```text
python -m autohls research matmul --backend native --budget 2
```

上述命令创建新的实验输出。只读网页不会启动板卡或云任务。源码包没有附入历史数据，其开箱状态如下。

| 页面/入口 | 仅源码包的开箱状态 | 展示数据所需条件 |
| --- | --- | --- |
| `/` 主工作台 | 可用 | 导入随包示例，展示未校准的公式估算与规则/Pareto流程 |
| `autohls.channel_replay.replay()` | 可用 | 直接复算三场景确定性合成参考，无板卡数据 |
| `/replay.html` | 静态页面可打开，默认显示证据不可用 | 六份原字节软件回放文件置于原相对目录可画曲线，未附板测时明确 `board_verified=false`；附板测则强制核对完整先前交付/板测绑定 |
| `/evidence.html` | 静态页面可打开，默认 `available=false` | 对应批次的真实 `audit.json` / `final-audit.json` 才能显示审计快照；快照显示不等于本机重新核验完整工具树 |
| `/delivery.html` | 静态页面可打开，默认 `available=false` | 需要 `active.json`、交付选择与冻结输入；声称固件和板卡通过还需要对应完整构建/板测归档 |

应用页的软件参考、板级功能证明和搜索审计快照具有不同数据依赖。缺失数据不使用工作台公式或合成夹具补齐，不将纯软件复算显示为实板成功。可另附原字节真实数据包供离线演示，但不得改写历史摘要或用不完整摘录冒充全链复核。

## 当前实现范围

- 四个固定契约内核：32×32 矩阵乘、FIR、二维卷积、256 点整数前缀和。
- 30 点受控配置空间，参数为流水 II、循环展开和数组分区；模型只选择合法剩余候选 ID。
- 原生测试、C 仿真、HLS 报告解析、资源门限、Pareto 排序、独立 RTL 筛选及已测候选回退。
- 五批固定预算协议、原始失败保留、逐文件摘要、归档审计、报告和搜索曲线复算。
- PYNQ-Z2 的 AXI 主接口、AXI-Lite 控制、批处理包装、C/Python 驱动、保护区及缓存一致性检查。
- 32 通道增益/串扰补偿的合成数据回放页面；固定手设系数，没有实采或自动学习的声明。

矩阵乘的历史批量 64 板级计时支持约 1.263 倍的限定吞吐收益；较小批量仍慢于 CPU。模型实测未证明普遍优于同预算随机搜索。HLS 估计、RTL 功能验证和板级计时分别记录，不能互相代替。

## 目录

| 目录/文件 | 内容 |
| --- | --- |
| `autohls/` | 引擎、配置、工具解析、候选提议、实验与回放模型 |
| `benchmarks/`、`examples/` | 独立 C++ 测试台与固定算法契约实现 |
| `hardware/` | AXI 包装、Vivado Tcl、驱动、验收、打包与板卡 XML |
| `scripts/` | 搜索协议、审计、报告、云端启动器及 Jupyter 传输工具 |
| `web/`、`server.py` | 本地工作台与只读证据/交付/应用页面 |
| `tests/` | Python 单测与 Node 界面测试，均可离线运行 |
| `docs/` | 选定契约、冻结实验计划、交付与边界说明 |
| `SOURCE_MANIFEST.json` | 原件与提交副本摘要、派生差异、排除范围 |
| `VERIFICATION.md` | 本提交副本的实际离线核验结果 |

## 依赖与板卡操作

核心 CLI 与网页不依赖第三方 Python 包。运行完整桌面单测或 Jupyter 客户端可在独立虚拟环境安装 `requirements.txt`。板端驱动使用已有 PYNQ 2.5 镜像内的 PYNQ/NumPy；不要在既有板卡镜像上套用桌面依赖文件。

`scripts/board_jupyter.py` 要求通过 `--base-url` 指定已确认的板卡 HTTP origin，密码仅从标准输入读取，不内嵌部署地址或保存密码。实际执行硬件脚本前，必须确认板型、固件版本、接线及 PL 独占访问；本包没有自动开始板测的入口。硬件超时后保留状态，不盲目重放或释放仍可能使用的缓冲区。旧首次验收 notebook 已排除。

云端脚本保留了历史验证环境的通用数据盘布局和 Vivado 2019.1 启动方式；需要使用者自行建立合法工具环境和符合其部署的目录，不含云账号、认证配置或服务器 IP。

## 测试

```text
python -m unittest discover -s tests -v
node --test tests/test_evidence_ui.js tests/test_delivery_ui.js tests/test_replay_ui.js
```

需要外部编译器、Linux 或完整实验归档的测试有条件地跳过。模拟单测和合成夹具用于代码验证，不充当实测结果。完整历史归档、生成候选和工具生成工程留存在原项目归档；代码包包含可生成与审计这些数据的当前源代码。

本目录是 2026-10-07 的匿名提交派生树，不是历史实验的冻结源包。五份原件作了必要的部署/路径派生修改或对应的验证调整，其余收录原件逐字节保留；详见摘要清单。第三方声明见 `THIRD_PARTY_NOTICES.md`。项目原创部分未新增开源许可，比赛的参赛承诺及授权另按官方表单签署。
