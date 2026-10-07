# 候选呈现排列选项：离线验收，不代表搜索收益

## 为什么只改这一项

[12次固定状态诊断](MODEL_ORDER_PROBE_20260927.md)观察到A/B各自重复一致、跨条件选择不同，支持把呈现顺序作为独立实验因素。它不证明随机排列更优。因此保留原默认行为，仅新增一个可选参数；不加入覆盖调度、静态候选过滤、提示重写或额外工具调用。

## 接口和预算边界

`research --candidate-order-seed <整数>` 仅供 `--planner ollama --backend hls` 使用，默认省略。模型生成的 `--seed` 保持独立，不复用为排列种子。第0次评估始终是计入预算的基线；第i次提议对当前剩余候选列表的副本执行 `random.Random(排列种子+i).shuffle(...)`，i从1开始。

同一副本同时用于提示的候选列表及输出JSON Schema枚举。候选集合、配置内容、源码、观测顺序、约束、目标、生成参数及排名不变；不修改调用者列表。失败综合仍占预算，非法模型结果仍中止，不重试。无反馈组的 `observations=[]` 不变。

manifest增加 `candidate_order`，包含 `policy=canonical/remaining-shuffle-v1`、基础种子和种子规则。启用后，每个提议trace保存实际 `candidate_order_seed`，request保存实际排列。未启用时旧请求内容和生成选项不变；新增manifest元数据与源码哈希变化不应冒充新测量。

只展示调用形式，下面命令**尚未作为新搜索执行**：

```text
python -m autohls research matmul --backend hls --planner ollama --model INSTALLED_MODEL --budget 8 --goal latency --seed 0 --candidate-order-seed 20260927 --output-dir NEW_DIRECTORY
```

同一基础种子、同一剩余列表及评估序号可复建同一排列。若模型此前选项不同，剩余集合会不同，不能声称整条轨迹因此确定。基础种子和评估序号使用加法，也不保证不同实验的种子序列互不相交；它不是独立性证明。

## 离线验收记录

核心代码仅修改 `autohls/planner.py`、`autohls/experiments.py`、`autohls/cli.py` 和相关测试；旧云端冻结源码与结果不修改。

- 新测试覆盖排列双射、输入列表不变、同种子重建、不同种子、schema顺序同步、无反馈隔离、生成种子独立、非法参数提前拒绝、基线/失败预算和逐步种子记录。
- 完整回归148项中146通过，2项Linux专用测试跳过；网页4项测试通过。
- [check_order_compatibility.py](../scripts/check_order_compatibility.py) 从已冻结的三个状态生成A/B共6份请求，拦截全部HTTP调用并主动报出 `OFFLINE: network deliberately disabled`，没有真实回复、模型调用、HLS或板级结果。
- 输出在 `artifacts/order-option-offline-20260927/v2`。[checks.json](../artifacts/order-option-offline-20260927/v2/checks.json) 中6项均通过LF归一化的文件字节核对，以及按原搜索引擎JSON序列化规则构造的HTTP字节核对。后者比较旧搜索请求的序列化规则；固定状态诊断执行器直接发送原pretty-JSON文件，空白形式不同，不能混称同一原始HTTP字节。
- 首次 `v1` 严格文件字节比较在matmul-A失败；原因为Windows `write_text` 的60个CRLF与云端LF不同。JSON完全相同，LF归一化后相同。该失败目录保留，未覆写；`v2`分别标明原始文件字节不相同与实际核对口径，不改动原输入。

复建入口如下；目录必须不存在。脚本只生成请求并拦截HTTP，不启动模型或HLS：

```text
python -m scripts.check_order_compatibility artifacts/model-order-probe-20260927/v1 artifacts/order-option-offline-20260927/NEW_REPLAY
```

## 下一次真实对照前

本次离线版本已冻结为119个文件，源码包位于 `artifacts/order-option-offline-20260927/source-v1/autohls-model-suite-source.tar.gz`，SHA-256 `a0ad8e9bcf68376b94ef6502d59a42d1c7f6c27c9b20c4ef2864ecc8f2863897`。从 `frozen-v1` 实际重建至 `replay-frozen-v1`，六个检查全部一致，`checks.json` SHA-256均为 `37f88b114e261cb24590519f77888578cfdc68aca453df0ba0a3d1ee4d71474e`。第一次冻结目录复建因沙箱不允许写入同项目父目录而拒绝；获得该目录写入权限后才执行成功，未绕过限制。

先冻结新源码、固定候选排列基础种子、预算及全部顺序。在同一新版引擎上匹配默认/排列条件，并分别包括有/无反馈，随机对照使用同一候选空间及预算。旧预算8/16只作为历史参照，不能代替新版同批控制。先预定全部实验，不看结果后增删种子、换排列或隐藏失败；全部入选配置仍须独立RTL。仅排列选项通过离线测试，不表示已证明搜索性能改善。
