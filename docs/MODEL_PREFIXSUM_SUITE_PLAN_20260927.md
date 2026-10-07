# 前缀和前瞻迁移：固定14组协议

2026-09-27。用户授权自行判断后续路线并持续推进。选择前缀和迁移验证，不继续调整三个开发内核；仅使用已授权现有CPU云主机，不新增资源、权重、付费API或板卡操作。现有主机继续按实际使用计费。本文件在新任务的真实模型/HLS观察之前固定。

## 完成标准与固定条件

沿用[已冻结契约](PREFIXSUM_CONTRACT_20260927.md)及32用例/16512检查/seed20260927测试台；30配置原生预检和12故障拦截已经完成。源码、测试台、契约SHA-256在 `scripts/model_prefixsum_suite.py` 中固定，任何不符均拒绝启动。显式 `research prefixsum` 可用；默认 `research all` 仍只有 matmul/fir/conv2d。

| 条件 | 次数 | 固定参数 | 模型请求上限 |
| --- | ---: | --- | ---: |
| 无覆盖随机 | 5 | seed0–4各一次 | 0 |
| 流水覆盖随机 | 5 | seed0–4各一次 | 0 |
| 流水覆盖模型无反馈 | 2 | seed0，temperature0，两次重复 | 14 |
| 流水覆盖模型有反馈 | 2 | seed0，temperature0，两次重复 | 14 |

共14组，每组预算8，最多112次评估、28次模型提议。基线、失败综合、原生失败均占评估预算；提议异常终止该组，不重试、不替换、不追加种子。后续组按固定次序执行；身份不符或用户STOP则停止，不静默重启中断批次。

顺序与42组协议中单个内核完全一致：无覆盖随机s0、覆盖随机s0、无反馈r0、有反馈r0、有反馈r1、无反馈r1；再s1–4的两随机条件交替先后（奇数先覆盖，偶数先无覆盖）。每组重新评估，不复用其他组性能缓存。模型重复不当作独立随机种子；不同随机策略不作逐seed配对。

覆盖规则仍是 `pipeline-warmup-v1`：第一次无pragma基线，第二次候选池仅II=1，第三次仅II=2，之后全部剩余配置。随机在该池均匀抽样；模型的候选池和schema相同。无覆盖随机保留一次shuffle。保持规范顺序，不新增静态合法性过滤；无反馈observations严格为空。模型提示、参数、约束和错误摘要生成代码均沿用42组版本，仅新增显式内核契约入口。

器件 `xc7z020clg400-1`，时钟10ns，时延主目标，资源限额 LUT40000/FF80000/DSP180/BRAM_18K200。工具 Vivado HLS 2019.1；现有模型 `autohls-qwen-coder:7b-q4km-ms9bc02b77`，digest `d589b66e0bb670ee449736b6a327f14aaac1ea29860a89aa39f3967774d1c987`，Ollama0.34.1。模型只监听云端127.0.0.1:11434，由批次拥有并在退出时停止。不修改认证、安全组或关闭云主机。

## 预定比较与证据边界

分别比较随机覆盖增量、同覆盖模型无反馈/有反馈相对覆盖随机、同覆盖反馈增量，共四项。只有两侧全部计划运行完成预算且有可行结果才计算组中位数相对变化。报告实际评估、原生通过、综合、约束可行、错误次数及模型/评估/总时间，不能用模型理由代替测量。

**不执行30点HLS参考枚举**。`reference_policy=not_measured`；参考值、命中次数、最优差距为null，报告“未测量”，不能解释为“未达到”或0。主比较只使用本批匹配条件，不把历史三个开发内核作控制。

14组搜索结束后先审计原始输入、候选、随机轨迹、覆盖池、请求/响应、无反馈隔离、工具XML/Tcl及错误；有缺失或审计故障先留档，不补造或替换结果。确认模型服务已退出后，在同一冻结目录单次执行独立RTL。先检查基线与各组已观测入选方案，相同源码/测试台去重；被拒绝则在该组已测可行排名中回退，保留原HLS最佳。最多验证30个不同配置，不增加搜索预算。已有RTL目录禁止重跑。最终证据包校验包摘要及逐文件哈希，以冻结代码离线审计并复算报告。

这只是一个事先未参与本项目策略调参的迁移任务，不保证模型预训练未见，也不证明广泛泛化或显著性。观察结果后若再调策略，该任务即成为开发任务。HLS估计与RTL正确性不是板级计时、时序收敛或功耗证据。

## 执行目录与入口

- 云端唯一新目录：`/mnt/datadisk0/autohls/releases/autohls-20260927-prefixsum-suite-v1`，已存在则拒绝覆盖。
- 本地：`artifacts/model-prefixsum-suite-20260927/launch-v1`、`frozen-launch-v1`、`v1`；旧批次、原生预检冻结包不动。
- 专用SSH密钥及既有host alias/ED25519/严格主机检查保持不变。

```text
python -m scripts.model_prefixsum_suite prepare
python -m scripts.model_prefixsum_suite run
python -m scripts.model_prefixsum_suite export RELEASE_ROOT
python -m scripts.model_prefixsum_suite audit EVIDENCE_ROOT NEW_AUDIT_JSON
python -m scripts.model_prefixsum_suite rtl RELEASE_ROOT
python -m scripts.report_model_suite FINAL_AUDIT_JSON FINAL_EVIDENCE_ROOT NEW_REPORT_DIR
```

先用离线临时合成夹具测试14组运行/预算/失败/STOP/拒绝回退/归档/报告。夹具数据只存在临时测试目录，不作为实验成绩。随后冻结、只读核验现有主机空闲与磁盘和工具环境，再启动一次。导出仅含结束组，不读取活动组作为完整结果；用户要求停止时在本批 `artifacts/model-suite/STOP` 建标记，当前组结束后退出。不得擅自停止云主机。
