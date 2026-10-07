"""Software batching of the reviewed 32x32 accelerator; no implicit board access.

The FPGA still launches once per matrix. Only bulk copies and cache calls are
amortized. Compatible with Python 3.6 / PYNQ 2.5; owners survive ambiguous faults.
"""
import ctypes
from pathlib import Path
import time

try:
    from . import board_benchmark as bench
except ImportError:
    import board_batch_support as bench

_retained_buffers = []


def _batches(values, batch_size, warmup_matrices):
    import numpy as np
    if type(batch_size) is not int or batch_size not in (1, 4, 16, 64):
        raise ValueError("Reviewed batch sizes are 1, 4, 16, 64")
    if type(warmup_matrices) is not int or warmup_matrices % batch_size or len(values) % batch_size:
        raise ValueError("Measured and warmup matrix counts must be whole batches")
    cases = bench._inputs(values, warmup_matrices)
    return [tuple(np.stack([row[port] for row in cases[start:start + batch_size]]) for port in range(3))
            for start in range(0, len(cases), batch_size)]


def _result(rows, batch_size, warmup_matrices, **metadata):
    metadata.update(batch_size=batch_size, measured_batches=len(rows),
                    warmup_batches=warmup_matrices // batch_size,
                    measured_matrices=len(rows) * batch_size, warmup_matrices=warmup_matrices,
                    checks=(len(rows) * batch_size + warmup_matrices) * 1024,
                    measurements=rows,
                    statistics_us={key: bench.distribution([row[key] for row in rows])
                                   for key in rows[0] if key.endswith('_us')})
    return metadata


def run_cpu(library, values, batch_size, warmup_matrices=64):
    import numpy as np
    batches = _batches(values, batch_size, warmup_matrices)
    function = ctypes.CDLL(str(Path(library).resolve())).matmul_cpu_batch_ns
    pointer = ctypes.POINTER(ctypes.c_int32)
    function.argtypes, function.restype = [pointer] * 3 + [ctypes.c_uint32], ctypes.c_uint64
    arrays = [np.empty((batch_size, 32, 32), dtype=np.int32) for _ in range(3)]
    addresses = [array.ctypes.data_as(pointer) for array in arrays]
    rows = []
    for index, (a, b, reference) in enumerate(batches):
        start = time.perf_counter()
        arrays[0][:], arrays[1][:] = a, b
        arrays[2].fill(0)
        compute_ns = int(function(*addresses, batch_size))
        output = np.array(arrays[2], copy=True)
        elapsed = (time.perf_counter() - start) * 1e6
        if compute_ns == 2 ** 64 - 1:
            raise RuntimeError("CPU batch clock or count error")
        if not np.array_equal(output, reference):
            raise AssertionError("CPU batch output mismatch at " + str(index))
        if index >= warmup_matrices // batch_size:
            rows.append({'batch': index - warmup_matrices // batch_size, 'compute_us': compute_ns / 1000.,
                         'end_to_end_us': elapsed, 'per_matrix_us': elapsed / batch_size})
    return _result(rows, batch_size, warmup_matrices, participant='cpu',
                   timing_note='One C call per batch; copies and prefill included; allocation/packing/checking excluded')


def run_fpga(directory, values, batch_size, warmup_matrices=64, confirm_board=False, cacheable=False):
    if confirm_board is not True:
        raise RuntimeError("Board use not confirmed")
    if _retained_buffers or bench._retained_buffers or bench.board_test._retained_buffers:
        raise RuntimeError("Previous operation may be active; do not rerun")
    if type(cacheable) is not bool:
        raise ValueError("cacheable must be bool")
    batches = _batches(values, batch_size, warmup_matrices)
    manifest = bench.board_test.inspect_bundle(directory)
    import numpy as np
    import pynq
    if not str(pynq.__version__).startswith('2.5'):
        raise RuntimeError("Requires PYNQ 2.5")
    overlay = pynq.Overlay(str(Path(directory) / 'autohls_matmul.bit'))
    ip = overlay.matmul_axi_0
    if overlay.ip_dict['matmul_axi_0']['phys_addr'] != manifest['control_base']:
        raise RuntimeError("Unexpected AXI control address")
    clock = float(pynq.Clocks.fclk0_mhz)
    if abs(clock - manifest['clock_mhz']) > .1:
        raise RuntimeError("Unexpected FPGA clock")
    buffers, rows, may_be_active = [], [], False
    try:
        for _ in range(3):
            buffers.append(pynq.allocate(shape=(batch_size, 32, 32), dtype=np.int32, cacheable=int(cacheable)))
        for buffer in buffers:
            if bool(buffer.cacheable) != cacheable:
                raise RuntimeError("Unexpected cache policy")
            address = int(buffer.physical_address)
            if address % 4 or address < 0x100000 or address + buffer.nbytes > 0x20000000:
                raise ValueError("Buffer outside reviewed DDR map")
        arrays = [np.asarray(buffer) for buffer in buffers]
        for array, buffer in zip(arrays, buffers):
            if array.ctypes.data != buffer.ctypes.data or array.nbytes != buffer.nbytes:
                raise RuntimeError("Expected zero-copy views")
        # Fixed buffer addresses; no preloading inputs outside the timed region.
        addresses = [tuple(int(buffer.physical_address) + i * 4096 for buffer in buffers)
                     for i in range(batch_size)]
        offsets = [manifest['registers'][port] for port in ('a', 'b', 'c')]
        for index, (a, b, reference) in enumerate(batches):
            may_be_active = True
            if not ip.read(0) & 4:
                raise RuntimeError("Accelerator not idle before copying")
            may_be_active = False
            start = time.perf_counter()
            arrays[0][:], arrays[1][:] = a, b
            arrays[2].fill(0)
            for buffer in buffers:
                buffer.flush()
            launch_us = 0.
            for pointers in addresses:
                may_be_active = True
                if not ip.read(0) & 4:
                    raise RuntimeError("Accelerator not idle before pointer writes")
                may_be_active = False
                for offset, address in zip(offsets, pointers):
                    ip.write(offset, address)
                may_be_active = True
                launch_us += bench.board_test._launch_wait(ip, 2.0)
                if not ip.read(0) & 4:
                    raise RuntimeError("Done but not idle; retain batch buffers")
                may_be_active = False
            buffers[2].invalidate()
            output = np.array(arrays[2], copy=True)
            elapsed = (time.perf_counter() - start) * 1e6
            if not np.array_equal(output, reference):
                raise AssertionError("FPGA batch output mismatch at " + str(index))
            if index >= warmup_matrices // batch_size:
                rows.append({'batch': index - warmup_matrices // batch_size, 'launch_wait_sum_us': launch_us,
                             'end_to_end_us': elapsed, 'per_matrix_us': elapsed / batch_size})
    finally:
        if may_be_active:
            _retained_buffers.append((overlay, buffers))
        else:
            for buffer in buffers:
                buffer.freebuffer()
    return _result(rows, batch_size, warmup_matrices,
                   participant='fpga_cached' if cacheable else 'fpga_uncached', cacheable=cacheable,
                   clock_mhz=clock, bit_sha256=manifest['sha256']['autohls_matmul.bit'],
                   timing_note='Software batch, one checked MMIO launch per matrix; bulk copy/flush/invalidate; packing/allocation/load/checking excluded')
