# AutoHLS PYNQ-Z2 首次上板验收包

> 2026-09-26 更新：辅助复位极性缺陷已修复。修正版 baseline32 / optimized32 均已在真实 PYNQ-Z2 上通过 20 组验收，共 40,960 项输出校验。**不要再加载 2026-09-23 的旧基线或优化版。** 详情见 [验收与故障记录](../docs/BOARD_ACCEPTANCE_20260926.md)；历史归档保持原样，新版检查器会拒绝旧故障配置。

本次通过的新包为 `artifacts/resetfix-20260926/pynq-z2-resetfix.zip`。实测结果保存在独立 [结果归档](../artifacts/resetfix-20260926/board-results-20260926.zip)，按 bit SHA-256 与包内 manifest 关联；冻结包中构建时的 `board_verified=false` 不回写。旧包的单核 C/RTL 仿真没有覆盖板级复位连接，因此不能用旧包替代修复版。

后续 [CPU/两套 FPGA 重复性能对照](../docs/BOARD_BENCHMARK_20260926.md) 已完成：各 500 次正式测量并逐次校验。优化版端到端中位数优于 FPGA 基线，但仍慢于本次 CPU 对照。新增 `board_benchmark.py` 与 `matmul_cpu.c` 不在此前冻结 ZIP 中，性能实验源码与结果另行归档；不要混改原 ZIP。

同日追加 [驱动开销分析](../docs/DRIVER_PROFILE_20260926.md)：`board_benchmark.run_fpga` 支持可选 `plain_views=True`，预建共享 ndarray 视图使同轮端到端中位数减少 29.03%，但仍慢于 CPU。保持 `cacheable=False, profile=False`；缓存实验反而更慢，`profile=True` 只用于诊断。三个选项默认均为 False，显式板卡确认及所有缓存/空闲/故障保留检查不变。不要把普通 ndarray 虚拟地址写到硬件指针寄存器，仍由原 CMA 所有者提供物理地址。

后续 [软件批处理实验](../docs/BATCH_BENCHMARK_20260926.md) 使用独立 `board_batch.py` 和 `matmul_cpu_batch.c`，固定批量 1/4/16/64。单组保持非缓存较快；较大批量可显式试用缓存，但所有 flush/invalidate 必须保留。批量 64 测得 0.461 ms/组（整批时间摊销），仍慢于同批量 CPU；FPGA 仍每组启动一次，不是真正的硬件批处理。导入不操作板卡，调用仍需显式确认，故障时保留整批缓冲区且不可直接重试。

新 [硬件批处理独立包](../docs/HARDWARE_BATCH_20260926.md) 已通过离线 C/RTL、100 MHz 布线和 [后续实板验收](../docs/HARDWARE_BATCH_BOARD_20260926.md)。其 schema 为 2，新增 `batch_count=0x28`，必须使用 `board_hardware_batch.py`，不能用本页旧单组 notebook 或 `board_batch.py` 驱动；旧检查器会拒绝。不要手工删减 manifest 绕过检查。批量 64 的缓存硬件路径在五轮同板对照中约为 CPU 的 1.263× 速度，小批量仍慢。

PYNQ 2.5 同名 bit 文件共享 `/lib/firmware` 缓存：切换配置时必须重新构造全新 `Overlay(..., download=False)` 后下载，不能对旧对象重复 `.download()`；本次新驱动另在分配前核对暂存固件内容。路径/时间戳匹配不等于配置内容正确。加载前仍须确认事务空闲、无故障保留缓冲区，不能因增加了哈希校验就省略安全检查。该规则由本次现场故障及修复验证得到，失败记录没有删除。

## 使用修复包再次验收的步骤（需确认板卡可用）

1. 保持现有 PYNQ 2.5 系统，不重刷 SD 卡。把整个包放到板卡 Jupyter 文件夹的独立目录，不能只上传 notebook。
2. 两个目录 `baseline32/`、`optimized32/` 各有同名配对的 `autohls_matmul.bit` 与 `.hwh`。不要互换文件。核对板卡确实为 PYNQ-Z2；不要用于其他板型。
3. 打开 `02_matmul_acceptance.ipynb`，先执行离线校验单元。默认 `CONFIRM_BOARD = False`，批量运行也不会加载 bitstream。
4. 确认没有其他 notebook 在访问 PL、无外接 PMOD/Arduino 电路，保存所有其他工作，再主动把确认值改为 True。加载自定义 overlay 会替换 LED 测试使用的 base overlay；无需外接传感器、摄像头或显示器。
5. 分别执行基线和优化版的 20 组验收，输出与 NumPy int64 参考结果逐项比较。结果自动保存到新建的时间戳目录，不覆盖旧记录。

## 接口与计时范围

- PS GP0 控制 AXI-Lite，HLS AXI master 经 HP0 访问 PS DDR，不需要 AXI DMA IP。FCLK0 为 100 MHz。
- 寄存器取自本次 HLS 生成的 `xmatmul_axi_hw.h`：control=0x00，a=0x10，b=0x18，c=0x20；控制基址 0x43C00000，经 HWH 再核对。
- DDR 输入为两个 32×32 的 `int32` 行优先数组；有效域 [-1000,1000]，运算内核为 int16×int16、int32 累加。输出为 32×32 int32。
- 必须用 `pynq.allocate` 的连续缓冲区和实际物理地址；输入/输出缓存按程序 flush/invalidate，不能传普通 NumPy 虚拟地址。
- `launch_wait_us` 含 Python 控制写入与轮询；`end_to_end_us` 还含缓冲区拷贝、缓存维护及结果拷贝。不含 overlay 加载、分配和参考计算，均不是纯硬件周期计数。这里没有声称 CPU 加速比。
- 两套文件的模型、输入种子、尺寸、精度、接口和时钟一致；初次 20 组正确性验收的顺带计时不作为性能统计。后续重复对照另含预热、5 批测量和单线程优化 CPU 基线，见上方报告，仍不代表纯硬件周期或跨平台性能。

## 停止和恢复

如出现超时、输出错误、AXI 访问卡住：停止继续运行，不再重载 overlay，不拔电试错。超时/中断时驱动保留缓冲区，防止正在运行的 AXI master 访问已释放内存。不要清除 `_retained_buffers` 或重启 notebook 内核以强行重试；保存错误，由后续步骤指导板卡复位/安全关机恢复。若软件终端还能正常响应，先保存记录并执行正常系统关机，等待完成后再断电。不要在事务中改变 PL 时钟或复位。

Vivado 报告保留了 Xilinx AXI FIFO 相关 `REQP-1840`（异步复位信号连接 RAMB）及 `RTSTAT-10` 警告；优化版另有 `DPOP-1/2` DSP 流水寄存器建议。基线共 13 项警告，优化版共 21 项，无 Error/Critical Warning，未降低严重级别或隐藏；时序及本次功能测试通过不等于已覆盖所有异步复位瞬间行为。详见各目录 `drc.rpt`。

API 依据：[PYNQ 2.5 allocate](https://pynq.readthedocs.io/en/v2.5/pynq_libraries/allocate.html)、[buffer](https://pynq.readthedocs.io/en/v2.5/pynq_package/pynq.buffer.html)、[Overlay](https://pynq.readthedocs.io/en/v2.5/overlay_design_methodology/overlay_tutorial.html)。软件替身测试只验证驱动控制和缓冲区生命周期，不是板卡验证。
