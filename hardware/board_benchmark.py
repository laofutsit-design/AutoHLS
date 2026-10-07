"""PYNQ 2.5 repeated matmul measurement; import never accesses hardware.

Caller controls round order, CPU affinity and exclusive result persistence.
Input generation, reference calculation, allocation and programming are untimed.
"""
import ctypes
import hashlib
import math
from pathlib import Path
import statistics
import time

try:
    from . import board_test
except ImportError:
    import board_test

_retained_buffers = []


def dataset(seed, samples=100):
    import numpy as np
    if not isinstance(samples, int) or not 1 <= samples <= 1000:
        raise ValueError("samples must be an integer in [1,1000]")
    rng = np.random.RandomState(seed)
    values, digest = [], hashlib.sha256()
    for index in range(samples):
        a = rng.randint(-1000, 1001, size=(32, 32)).astype(np.int32)
        b = rng.randint(-1000, 1001, size=(32, 32)).astype(np.int32)
        if index == 0:
            a.fill(0)
        elif index == 1:
            a.fill(1000)
            b.fill(1000)
        elif index == 2:
            a.fill(-1000)
            b.fill(1000)
        elif index == 3:
            b = np.eye(32, dtype=np.int32)
        digest.update(a.astype("<i4", copy=False).tobytes())
        digest.update(b.astype("<i4", copy=False).tobytes())
        values.append((a, b, np.dot(a.astype(np.int64), b.astype(np.int64))))
    return values, digest.hexdigest()


def distribution(values):
    values = sorted(values)
    if not values or any(not math.isfinite(x) or x < 0 for x in values):
        raise ValueError("Expected nonnegative finite measurements")
    return {"count": len(values), "min": values[0], "median": statistics.median(values),
            "p95": values[int(math.ceil(len(values) * .95)) - 1], "max": values[-1]}


def _inputs(values, warmup):
    import numpy as np
    if not values or not isinstance(warmup, int) or not 0 <= warmup <= len(values):
        raise ValueError("Invalid dataset/warmup")
    for a, b, reference in values:
        for array in (a, b):
            if array.shape != (32, 32) or array.dtype != np.int32 or not array.flags.c_contiguous:
                raise ValueError("Expected contiguous 32x32 int32 inputs")
            if np.any(array < -1000) or np.any(array > 1000):
                raise ValueError("Input outside reviewed non-overflowing domain")
        if reference.shape != (32, 32) or reference.dtype != np.int64:
            raise ValueError("Expected 32x32 int64 reference")
    return values[:warmup] + values


def _result(variant, measurements, warmup, **metadata):
    keys = [key for key in measurements[0] if key.endswith("_us")]
    metadata.update(variant=variant, measured_cases=len(measurements), warmup_cases=warmup,
                    checks=(len(measurements) + warmup) * 1024, measurements=measurements,
                    statistics_us={key: distribution([row[key] for row in measurements]) for key in keys})
    return metadata


def run_cpu(library, values, warmup=20):
    import numpy as np
    cases = _inputs(values, warmup)
    function = ctypes.CDLL(str(Path(library).resolve())).matmul_cpu_ns
    pointer = ctypes.POINTER(ctypes.c_int32)
    function.argtypes, function.restype = [pointer] * 3, ctypes.c_uint64
    buffers = [np.empty((32, 32), dtype=np.int32) for _ in range(3)]
    addresses = [buffer.ctypes.data_as(pointer) for buffer in buffers]
    measurements = []
    for index, (a, b, reference) in enumerate(cases):
        start = time.perf_counter()
        buffers[0][:], buffers[1][:] = a, b
        buffers[2].fill(0)
        elapsed_ns = int(function(*addresses))
        output = np.array(buffers[2], copy=True)
        elapsed_us = (time.perf_counter() - start) * 1e6
        if elapsed_ns == 2 ** 64 - 1:
            raise RuntimeError("CPU clock_gettime failed")
        if not np.array_equal(output, reference):
            raise AssertionError("CPU output mismatch at iteration " + str(index))
        if index >= warmup:
            measurements.append({"case": index - warmup, "cpu_compute_us": elapsed_ns / 1000.0,
                                 "end_to_end_us": elapsed_us})
    return _result("cpu_ikj_o3", measurements, warmup,
                   timing_note="C compute timer plus Python input/output copies; single thread, warmed cached CPU arrays")


def run_fpga(directory, values, warmup=20, confirm_board=False, cacheable=False, profile=False,
             plain_views=False):
    if confirm_board is not True:
        raise RuntimeError("Board use not confirmed")
    if _retained_buffers or board_test._retained_buffers:
        raise RuntimeError("Previous operation may be active; do not rerun")
    if any(type(option) is not bool for option in (cacheable, profile, plain_views)):
        raise ValueError("cacheable, profile and plain_views must be bool")
    cases = _inputs(values, warmup)
    manifest = board_test.inspect_bundle(directory)
    import numpy as np
    import pynq
    if not str(pynq.__version__).startswith("2.5"):
        raise RuntimeError("Requires PYNQ 2.5")
    overlay = pynq.Overlay(str(Path(directory) / "autohls_matmul.bit"))
    ip = overlay.matmul_axi_0
    if overlay.ip_dict["matmul_axi_0"]["phys_addr"] != manifest["control_base"]:
        raise RuntimeError("Unexpected AXI control address")
    clock = float(pynq.Clocks.fclk0_mhz)
    if abs(clock - manifest["clock_mhz"]) > .1:
        raise RuntimeError("Unexpected FPGA clock")
    buffers, measurements, may_be_active = [], [], False
    try:
        for _ in range(3):
            buffers.append(pynq.allocate(shape=(32, 32), dtype=np.int32, cacheable=int(cacheable)))
        for buffer in buffers:
            if bool(buffer.cacheable) != cacheable:
                raise RuntimeError("Allocator did not provide the requested cache policy")
            address = int(buffer.physical_address)
            if address % 4 or address < 0x100000 or address + buffer.nbytes > 0x20000000:
                raise ValueError("Buffer outside reviewed PS DDR map")
        # Keep the owning PynqBuffers for cache maintenance and fault retention.
        arrays = [np.asarray(buffer) for buffer in buffers] if plain_views else buffers
        if plain_views:
            for array, buffer in zip(arrays, buffers):
                if array.ctypes.data != buffer.ctypes.data or array.nbytes != buffer.nbytes:
                    raise RuntimeError("Expected zero-copy views of the owned buffers")
        for index, (a, b, reference) in enumerate(cases):
            may_be_active = True  # Retain buffers if the idle read itself cannot return.
            if not ip.read(0) & 4:
                raise RuntimeError("Accelerator is not idle")
            may_be_active = False
            start = time.perf_counter()
            marks = [start] if profile else None
            arrays[0][:], arrays[1][:] = a, b
            if profile:
                marks.append(time.perf_counter())
            arrays[2].fill(0)
            if profile:
                marks.append(time.perf_counter())
            for port, buffer in zip(("a", "b", "c"), buffers):
                buffer.flush()
                if profile:
                    marks.append(time.perf_counter())
                ip.write(manifest["registers"][port], int(buffer.physical_address))
                if profile:
                    marks.append(time.perf_counter())
            may_be_active = True
            launch_us = board_test._launch_wait(ip, 2.0)
            if profile:
                marks.append(time.perf_counter())
            if not ip.read(0) & 4:
                raise RuntimeError("Done but not idle; buffers retained")
            may_be_active = False
            if profile:
                marks.append(time.perf_counter())
            buffers[2].invalidate()
            if profile:
                marks.append(time.perf_counter())
            output = np.array(arrays[2], copy=True)
            end = time.perf_counter()
            if profile:
                marks.append(end)
            elapsed_us = (end - start) * 1e6
            if not np.array_equal(output, reference):
                raise AssertionError("FPGA output mismatch at iteration " + str(index))
            if index >= warmup:
                row = {"case": index - warmup, "launch_wait_us": launch_us, "end_to_end_us": elapsed_us}
                if profile:
                    stages = ("copy_inputs", "clear_output", "flush_a", "write_a", "flush_b", "write_b",
                              "flush_c", "write_c", "launch_call", "idle_check", "invalidate", "copy_output")
                    row.update({stage + "_us": (after - before) * 1e6
                                for stage, before, after in zip(stages, marks, marks[1:])})
                measurements.append(row)
    finally:
        if may_be_active:
            _retained_buffers.append((overlay, buffers))
        else:
            for buffer in buffers:
                buffer.freebuffer()
    return _result(manifest["variant"], measurements, warmup, clock_mhz=clock,
                   cacheable=cacheable, profiled=profile, plain_views=plain_views,
                   bit_sha256=manifest["sha256"]["autohls_matmul.bit"],
                   timing_note="Python MMIO/poll; end-to-end includes copy, flush/invalidate and idle confirmation; not hardware cycles. Profiled stages include instrumentation overhead.")
