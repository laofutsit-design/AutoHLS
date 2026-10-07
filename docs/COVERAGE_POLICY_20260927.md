# 流水覆盖调度：离线实现与验收

状态：2026-09-27，`pipeline-warmup-v1` 已实现，默认关闭。本轮没有调用模型、HLS、RTL 或板卡，也没有新增云资源；下列本机 C++ 结果不是硬件性能证据。真实搜索收益尚待独立协议验证，不与旧预算8/16或39组结果混合。

## 固定规则与对照

只改变前期候选池，不使用历史成绩或诊断过滤候选。评估索引从0开始，基线和失败均占一次预算。

| 评估索引 | 两组共享的允许候选池 | 随机组 | 模型组 |
| --- | --- | --- | --- |
| 0 | 固定基线 `p0-u1-a1` | 直接评估，不抽随机数 | 直接评估，不调用模型 |
| 1 | 未尝试的 II=1 配置 | 在池内均匀随机选择 | 在相同池内提议 |
| 2 | 未尝试的 II=2 配置 | 在池内均匀随机选择 | 在相同池内提议 |
| 3及以后 | 全部未尝试配置 | 在池内均匀随机选择 | 在相同池内提议 |

共享的是候选池生成规则；各组已有选择不同，后续剩余集合不保证逐项相同。失败也从剩余集合移除，不补测、不免费重试。非法模型提议保留原始错误并结束该次运行，标记预算未完成，不伪装成一次已执行评估。

不实施全程轮转、合法性过滤或呈现顺序重排；这些是不同干预，不能同时混入。预算至少3，支持 `random` / `ollama`，与 `--candidate-order-seed` 同用会在工具执行前拒绝。模型身份、反馈截断、输出约束和排名保持原规则；无反馈仍只收到空 `observations`。

这是人工确定的预算分配规则，不是模型自发学会探索。旧随机策略先打乱剩余列表，新策略每次从当前池抽取；固定各自seed可复现，但跨策略同seed不保证随机抽样逐项配对。不能因此声称统计上的配对增益。

## 入口与留证

本机已执行的全空间功能检查（输出另起目录，不能覆盖归档）：

```powershell
.\scripts\research.ps1 all --backend native --planner random --budget 30 --seed 0 --goal latency --coverage-policy pipeline-warmup-v1 --output-dir artifacts/coverage-replay
```

模型路线仍要求 `--backend hls --planner ollama --model ...`，并显式传入同一覆盖选项；本轮未执行。默认 `--coverage-policy none` 保持旧算法，旧manifest不增加coverage字段。

启用时manifest记录完整规则，每次选择前保存 `selection-NNN.json`（索引、策略、候选ID池）。原始源码、模型请求/响应、结果、错误、预算与哈希仍按原流程保存。

独立检查器不调用调度函数，而是从ID重建规则和随机序列，同时检查模型请求、schema、反馈隔离及选择证据：

```text
python -m scripts.check_coverage_run RUN_DIR --output NEW_CHECK_JSON
```

输出文件必须不存在。检查范围仅为调度及选择证据，不替代功能、HLS报告、RTL、模型身份或整批统计审计。旧 `audit_model_suite` 明确拒绝带coverage的运行，避免套用旧协议声称验收。

## 验收结果

证据根目录：[`artifacts/coverage-policy-20260927/v1`](../artifacts/coverage-policy-20260927/v1)。

- 10项覆盖专项测试通过：预算3/8/16/30、seed 0/7/42、非法组合、旧行为回归、失败预算、越池提议终止、无反馈隔离及重新计算哈希后的语义篡改检查。测试中的模型/HLS均为明确标记的替身，不是真实实验。
- 完整Python回归177项：175通过，2项Linux启动器测试在Windows跳过；[日志](../artifacts/coverage-policy-20260927/v1/final-tests.log)。前端回归9/9通过；[日志](../artifacts/coverage-policy-20260927/v1/ui-tests.log)。
- 旧A/B的6份冻结模型请求重放，序列化HTTP请求字节全部一致；[兼容报告](../artifacts/coverage-policy-20260927/v1/request-compatibility/checks.json)。文件原始换行因CRLF/LF不同，归一化后一致。没有真实HTTP调用。
- 三个内核各30个不同配置，实际MSVC编译和运行全部通过，共1,800个测试用例、885,600项输出比较。三组均完成预算，无硬件最优值、无模型调用；[功能与哈希复核](../artifacts/coverage-policy-20260927/v1/native-validation-v2.json)。
- 独立重建三组全部90次选择轨迹通过；[调度复核](../artifacts/coverage-policy-20260927/v1/native-schedule-checks.json)。三组各156个证据文件、共468项SHA-256一致。
- 旧预算8/16审计、39组最终审计/完整包/报告/轨迹六项锚点哈希未变化；[归档完整性检查](../artifacts/coverage-policy-20260927/v1/archive-integrity.json)。

源码另存为本轮目录内的 `source-snapshot/`，逐文件哈希在 `source-checksums.json`，可移交的同内容压缩包为 `source-snapshot.zip`。冻结副本的专项回归和原始运行轨迹复核分别留在 `frozen-coverage-tests.log`、`frozen-schedule-checks.json`；这些是离线复现材料，不是新云端批次协议。

| 内核 | 实际编译执行配置数 | 测试用例 | 输出比较 |
| --- | ---: | ---: | ---: |
| matmul | 30 | 600 | 614,400 |
| fir | 30 | 600 | 153,600 |
| conv2d | 30 | 600 | 117,600 |

原生编译器忽略HLS pragma，因此上述90点检查验证候选生成与C++功能，不证明pragma硬件语义、HLS可行性、时序或加速。`native-validation.json` 的初版聚合将文件计数错误序列化为多个1；保留初版，重新逐项验证哈希后生成 `native-validation-v2.json`，每组计数为156，测量文件未改。

## 下一阶段边界

追加真实实验前，应在新目录冻结同预算、同内核、同工具/模型、同资源约束的协议，并独立验收批次审计器：

1. `none`随机 vs 覆盖随机，用于衡量确定性调度的增量；不能只拿历史随机结果替代本轮控制。
2. 覆盖随机 vs 覆盖无反馈模型 vs 覆盖有反馈模型，用于衡量同规则下模型与反馈的增量。
3. 固定种子、重复次数、运行次序、失败处理和预算，结果好坏都完整保留；记录可行率、最终时延/资源、模型与工具时间，最终入选仍需独立RTL。

上述是待验证设计，不是已执行的批次。本轮没有创建新云端任务或真实批次协议，也未证明模型优势。三个内核均为开发集，不能据此声称对未见任务的泛化。
