# 升级实施：可追溯的 PYNQ-Z2 交付链

授权与范围：2026-09-27 用户要求开始实施、持续推进，普通步骤不再询问是否继续。以现有 CPU 云主机、Vivado/HLS 2019.1 和 PYNQ-Z2 为边界，不新增付费资源、不修改历史实验。国赛获奖不是可由软件测试保证的验收条件。

最新状态（2026-09-27 晚间）：本里程碑已完成。固定搜索候选 → 独立 RTL → 包装与布线 → 新固件下载 → 14 步实板功能验收 → 归档独立复算的证据链已贯通。下方执行记录保留各阶段当时的等待、失败和审批状态；它们不是当前阻塞。竞赛整体、应用场景和模型优势验证仍未完成。

## 本阶段的可验证目标

1. 从已归档搜索中选择一个经过独立 RTL 验证的矩阵乘方案；先用该批冻结代码重新审计，不能只信 JSON 中的通过标签。
2. 将搜索、回退链、候选源文件、测试台、包装接口与构建代码的摘要固定在新交付目录。源码改变、错误候选、越界路径、缺少证据时拒绝构建。
3. 在现有云端新目录单次构建：原生检查、AXI 包装 C/RTL 仿真、Vivado 布线、时序/接口/复位检查和独立封装；失败保留，不覆盖重试。
4. 构建通过不等于板测通过。固件、驱动和输入协议明确绑定；实际下载之前需要当前接线、无其他 PL 访问及空闲条件。未知事务状态时停止，不自动复位或释放缓冲区。
5. 页面展示真实交付阶段与证据，不将历史板测归到新 bit，不用演示公式填补缺失值。完成自动化回归、篡改/故障拒绝测试及离线复核。

选择规则在构建前固定：使用 coverage42 的 `matmul-feedback-pipeline-warmup-v1-s0-r0` 的独立 RTL 最终候选，不跨组挑最好成绩。本次是交付工程验证，不是新模型有效性实验，不追加模型调用或调整搜索规则。批处理接口属于确定性的已审查模板，不称为模型自动发明架构。

## 边界

- 目前仅支持已审查的 32×32 矩阵乘契约：输入 [-1000,1000]、int16 乘法/int32 累加、DDR int32、一次启动 1–64 组、100 MHz。
- 任意 C/C++ 重构、其他算子上板、合法性过滤策略的公平新实验和真实应用场景仍是后续独立工作，不能因为本交付链完成就改写为已完成。
- 旧 42/14 组及其他冻结目录保持只读。旧批处理包与 1.263× 历史 CPU 对照仍属于 09-26 的特定微基准。
- 哈希用于检测版本混用和意外更改，不是恶意伪造条件下的数字签名或物理 FPGA 配置回读。

## 执行记录

- 17:57 严格主机密钥检查 SSH 成功；无活动 Vivado/HLS/xsim/Ollama 批次，数据盘约 58 GiB 可用。尚未启动新构建或访问板卡。
- v1 源码包 357 项、SHA-256 `771837f918b597f2ffb412c9125fdd25fb15fc643e59bac13991f69cd3d4f443`；42 组与 RTL 冻结重审全部通过。选中 `p1-u8-a8`，源码 SHA-256 与旧硬件批处理内核相同；不声称新算法或性能改善。
- 本地 206 项回归：204 通过、2 项 Linux 专用跳过。云端 v1 的前置回归发现打包漏掉测试所需 `docs/PREFIXSUM_CONTRACT_20260927.md` 和 `server.py`，195 项中 9 个错误、18 个因本地归档不在云端等原因跳过。入口退出 1，未运行 HLS/布线/板卡。保留 v1，修复范围仅增加文档、网页及服务器依赖到源码包，并增加打包依赖回归；随后使用新 v2，不覆盖 v1。
- v2 冻结 401 项输入；隔离目录本地回归 207 项，197 通过、10 跳过，云端 207 项中 189 通过、18 跳过。跳过项包括不随源码包上传的历史归档、未安装 NumPy 的板卡模拟测试和平台限定检查；不是 207 项全部在云端执行通过。
- 18:22:33（UTC+8）云端 v2 单次构建退出 0。原生 ASan/UBSan 检查 11 个用例、720,896 次比较通过；AXI 包装 C 仿真和 Verilog 协同仿真通过。保留 Vivado HLS 2019.1 的 `core_revision` 导出失败及原始 Tcl；仅使用既有的工程内 revision=1 包装修复，不修改工具安装或系统时间。
- 100 MHz 布线时序通过：setup slack 2.579 ns、hold slack 0.019 ns，check_timing 所列 12 类检查计数均为零。DRC 有 21 项警告：DPOP-1×4、DPOP-2×4、REQP-1840×12、RTSTAT-10×1；没有 Error/Critical Warning，不能写成“零警告”。
- 435 项交付证据已导出至本地独立目录，包摘要、逐文件摘要、冻结输入、候选来源和 bit/HWH 绑定复核通过。新增独立审计器直接重读 C/RTL、XML、时序、DRC 和修复 Tcl；两次审计 JSON 逐字节一致。结束前只读确认云端无 Vivado/xsim/Ollama 活动进程，未关闭主机。
- 本次新固件未上传或下载到板卡，未访问板卡。本地已准备绑定 receipt 的 29 项运行时包和 14 步单次验收程序。当前等待现场安全状态确认，`board_verified` 保持 false，历史 1.263× 不写入新包。
- 最终本地回归 222 项：220 通过、2 项 Linux 限定跳过，记录于 `final-regression-v2.log`；网页 22 项全部通过，记录于 `frontend-regression.log`。覆盖来源/源码/摘要/RTL 错配、报告缺失与指标篡改、无确认拒绝、失败不重试、包不覆盖以及网页缺失值/过期响应。故障夹具仅在临时测试目录，不混入真实结果。浏览器实际核验页面离线通过、板测未验收，并保存 `delivery-page.png`。
- 用户随后明确确认当前接线及 PL 独占条件。只读连接检查：板卡 `/login` 返回 200，需密码登录；未认证 `/api/kernels` 返回 403。浏览器打开及一次恢复均超时，未取得认证内核清单，不能据此判断 PL 空闲。未上传文件、未加载固件、未读写 PL；需用户在板卡 Jupyter 中完成登录，不在聊天中提供密码。记录见 `board-v2/access-check-20260927.json`。同时发现本机当前两套 Python 均缺少可选客户端依赖 requests，尚未安装；可在登录恢复后修复此本地依赖，不能把依赖缺失解释为板卡故障。
- 用户回复“已登录”后，浏览器确认能看到 Jupyter Home 和 Logout。但 Running/New 控件未正常响应，API 页面导航被浏览器客户端拦截，未得到内核清单。拟用官方出厂密码进行一次客户端登录的命令被安全审批拒绝，**该命令未执行**；已明确请求用户授权，不通过其他途径绕过。仅在本机 `.tools/jupyter-client` 补齐 PyPI requests 2.34.2 及依赖，未改系统 Python；现有客户端/验收 10 项测试通过，新的只读预检脚本通过 Python 3.6 语法检查。仍未上传、加载或操作 PL。
- 用户随后明确回复“允许”，授权专用客户端使用官方出厂密码登录。19:12 起只读核验唯一既有内核空闲、无活动会话、无诊断或保留缓冲区；确认旧加速器 control=4、100 MHz 后才继续。密码仅从 stdin 读取，不存入报告，不复制浏览器会话。
- 在板卡独立目录 `/home/xilinx/jupyter_notebooks/autohls-delivery-board-20260927-v2` 核验 29 项运行时文件，单次下载新 Overlay。实际 bit SHA-256 绑定 v2 receipt；加载跟踪、暂存固件摘要和实际运算检查均保留。有效载荷 SHA-256 `14667e51c26b50e1843a11435777c2ecad21e4b3d0eca5ebc8159b371e070de0` 与历史批处理固件相同，不能声称新架构或新性能提升；该检查也不是 FPGA 配置物理回读。
- 19:17–19:19 按预定顺序单次执行 14 步：batch=1/2/3/4/16/63/64 × uncached/cached，每条件两次启动、guard=true、固定输入种子，全部通过。实际记录的输出比较 626,688 次、输入完整性比较 1,253,376 次、保护区比较 86,016 次，合计 **1,966,080 次**。没有失败重试、补种子或调整协议；仅功能/边界验收，不作本批 CPU 对照或稳健性能推断。
- 19:22 导出 211,543 字节的实板证据包，66 项文件（65 项数据加清单），下载包摘要与逐文件摘要全部匹配。使用与板上完全相同摘要的冻结验收程序，在本机重新生成输入摘要、复算原始样本统计及比较数，并独立检查下载跟踪、凭据绑定、空闲及缓冲区记录；两次输出逐字节一致。归档末态为 control=4、100 MHz、保留缓冲区 0；之后未重新启动板卡运算、重置或关机。板卡墙钟陈旧，时间以客户端 UTC 日志为准。
- `/api/delivery` 每次从原始归档重新审计，新页面显示 `board_functional_verified`；构建阶段原始 `delivery.json` 中 `board_verified=false` 保持不变，单独板测结果绑定其 SHA-256。新故障测试覆盖不安全路径、错误包摘要、缺步骤、统计篡改、不完整下载、非空闲及缓冲区残留、错误凭据、页面旧结果残留与虚假 CPU 加速值；夹具只在临时目录。本地全量 Python 232 项：230 通过、2 项 Linux 限定跳过；网页 25 项通过。实际浏览器核验并保存 `board-v2/delivery-page-verified.png`，只重启本地只读服务，没有改云端或安全组。

## 交付物与摘要

归档相对根目录：`artifacts/delivery-upgrade-20260927`。v1 失败记录与 v2 冻结代码都保留；后续界面和验收程序不回写冻结源码。

| 文件 | SHA-256 |
| --- | --- |
| `v2/delivery-source.tar.gz` | `b8cd01cc9272373cd9c9ea6d05b346931e9ef76d21b7bb4af04982bc7a1bb51c` |
| `v2/delivery-evidence.zip` | `530be52da4e895be329e03bd717f8d6c89a0af6f529d25d2f8fb919aab899e6c` |
| `v2/exported/artifacts/delivery/bundle/delivery.json` | `49726a34979db9e8a51a54a986f6156938fab49f488a0d8ad06ff2c2c7691711` |
| 包内 bit | `5d012f7c98f679163673d7aa27d3c0869ff7591ba503405345f2cc2535c822c3` |
| 包内 HWH | `9ae93d53b2e24ac60a952b8404708be7e9a6cb4ccd9b00fb3738875b0823e8c5` |
| `v2/independent-audit.json` / `independent-audit-recomputed.json` | `46715b98356bd987e7b2f8972de119c399f1e4eab9aec670cc3daa5fa46d2836` |
| `board-v2/pynq-z2-delivery.zip` | `a8f579107edece16fd2f6328efb054916e7fd7b7bb0ab1ada907f4d3b16aa71e` |
| `board-v2/delivery-board-evidence.zip` | `2199473c65aca2e37853436c00d458cd02b2b984756aef76a78a94bb317deab8` |
| `board-v2/independent-audit.json` / `independent-audit-recomputed.json` | `4dc88e343058ff6ad48a501a19423bc94a012b8c906b50ca6670a31594453064` |

交付页：[候选到固件](http://127.0.0.1:8000/delivery.html)。`/api/delivery` 只读、重新校验本地冻结源码、交付包及实板归档；缺少或损坏证据不显示成功，不启动远程任务或操作板卡。远程日志区与板卡末态都是归档快照，不是实时状态。原始板卡记录位于 `board-v2/results/acceptance`，全量回归日志为 `board-v2/final-regression.log`、`frontend-regression.log`。

## 离线复核入口

在项目根目录使用已配置的 Python 运行，无需云端、模型或板卡。最后一条命令的输出路径必须尚不存在，不覆盖已有审计记录。

```powershell
python -m hardware.delivery verify-input artifacts/delivery-upgrade-20260927/v2/release
python -m hardware.delivery verify-delivery artifacts/delivery-upgrade-20260927/v2/exported
python -m scripts.audit_delivery artifacts/delivery-upgrade-20260927/v2/exported artifacts/delivery-upgrade-20260927/v2/manual-audit-new.json
python -m scripts.audit_delivery_board artifacts/delivery-upgrade-20260927/board-v2 49726a34979db9e8a51a54a986f6156938fab49f488a0d8ad06ff2c2c7691711 artifacts/delivery-upgrade-20260927/board-v2/manual-audit-new.json
```

`scripts.prepare_delivery_board` 仅生成离线上传包。`hardware.delivery_acceptance` 不调用 Overlay/download；要求另行确认并加载准确固件后，传入已核验且空闲的 overlay。程序与驱动都绑定摘要，每一步先写独占 attempt 文件，失败不自动重试。

## 本次实板协议（已执行并复核）

1. 确认当前 PYNQ-Z2 稳定上电、网线直连、无外接 PMOD/Arduino 模块、无其他 notebook/程序访问 PL。之后只读核验当前内核、占用、时钟和平台状态。
2. 仅上传已校验的 board-v2 包，在独立新目录复核 29 项文件；不覆盖现有实验。加载时使用新的 `Overlay(download=False)`，确认固件缓存未复用，再执行一次有跟踪的下载。staged firmware 摘要验证不是 FPGA 物理配置回读。
3. 按固定顺序验收 batch=1/2/3/4/16/63/64，每个 batch 分别测 uncached/cached、两次启动、guard=true、固定 seed。14 步仅证明功能/边界。预定比较数现已与真实记录逐项复核一致：626,688 输出、86,016 guard、1,253,376 输入，合计 1,966,080；不是全输入域形式化证明。
4. 任何未知事务、超时或固件不匹配保留现场，不自动重新加载、复位或释放可能仍在使用的缓冲区。完整原始结果离线审计后才能标记本次 `board_verified=true`；CPU 对照和性能提升另需独立固定协议，不能由这 14 步得出。

新交付链验收之后，竞赛整体仍需应用场景、更多可推广策略验证和最终演示/报告。任意 C/C++ 自动重构、普遍模型优势与“国一水平”均未由当前结果证明。
