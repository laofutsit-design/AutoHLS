"""One FPGA launch per batch. Never loads/reloads an overlay; Python 3.6."""
from pathlib import Path
import hashlib
import time

try:
    from . import board_batch as software
    from .batch_contract import inspect_bundle
except ImportError:
    import board_batch as software
    from batch_contract import inspect_bundle

_retained_buffers = []


def verify_firmware(overlay):
    """Detect PYNQ 2.5's same-basename firmware cache collision before DMA.

    This verifies the staged file, not FPGA readback. The caller must also use a
    fresh Overlay object for each download so PYNQ actually stages this payload.
    """
    import numpy as np
    from pynq.pl_server.device import parse_bit_header
    payload = parse_bit_header(overlay.bitfile_name)['data']
    expected = hashlib.sha256(np.frombuffer(payload, 'i4').byteswap().tobytes()).hexdigest()
    if not overlay.firmware_path:
        raise RuntimeError('No staged firmware path')
    actual = hashlib.sha256(Path(overlay.firmware_path).read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError('Staged firmware mismatch; use a fresh Overlay object, do not reuse download')
    return actual


def run_fpga(directory, overlay, values, batch_size, warmup_matrices=64,
             confirm_board=False, cacheable=False, guard=False):
    if confirm_board is not True:
        raise RuntimeError('Board use not confirmed')
    if _retained_buffers or any(e._retained_buffers for e in (software, software.bench, software.bench.board_test)):
        raise RuntimeError('Previous operation may be active; do not rerun')
    if type(cacheable) is not bool or type(guard) is not bool:
        raise ValueError('cacheable and guard must be bool')
    if type(batch_size) is not int or not 1 <= batch_size <= 64:
        raise ValueError('batch_size must be an integer in [1,64]')
    if type(warmup_matrices) is not int or warmup_matrices % batch_size or len(values) % batch_size:
        raise ValueError('Measured and warmup matrix counts must be whole batches')
    cases = software.bench._inputs(values, warmup_matrices)
    manifest = inspect_bundle(directory)
    import numpy as np
    import pynq
    expected_bit = str((Path(directory) / 'autohls_matmul.bit').resolve())
    if not str(pynq.__version__).startswith('2.5'):
        raise RuntimeError('Requires PYNQ 2.5')
    if (not overlay.timestamp or not overlay.is_loaded() or overlay.bitfile_name != expected_bit or
            pynq.PL.bitfile_name != expected_bit):
        raise RuntimeError('Expected already-loaded, current batch overlay; no automatic reload')
    if overlay.ip_dict['matmul_axi_0']['phys_addr'] != manifest['control_base']:
        raise RuntimeError('Unexpected AXI control address')
    if abs(float(pynq.Clocks.fclk0_mhz) - 100) > .1:
        raise RuntimeError('Unexpected FPGA clock')
    firmware_sha256 = verify_firmware(overlay)
    ip = overlay.matmul_axi_0
    batches = [tuple(np.stack([row[p] for row in cases[start:start + batch_size]]) for p in range(3))
               for start in range(0, len(cases), batch_size)]
    buffers, rows, may_be_active = [], [], False
    try:
        for _ in range(3):
            buffers.append(pynq.allocate(shape=(batch_size + int(guard), 32, 32), dtype=np.int32,
                                         cacheable=int(cacheable)))
        for buffer in buffers:
            address = int(buffer.physical_address)
            if address % 4 or address < 0x100000 or address + buffer.nbytes > 0x20000000:
                raise ValueError('Buffer outside reviewed DDR map')
            if bool(buffer.cacheable) != cacheable:
                raise RuntimeError('Unexpected cache policy')
        full = [np.asarray(buffer) for buffer in buffers]
        for array, buffer in zip(full, buffers):
            if array.ctypes.data != buffer.ctypes.data or array.nbytes != buffer.nbytes:
                raise RuntimeError('Expected zero-copy views')
        arrays = [array[:batch_size] for array in full]
        pointers = [int(buffer.physical_address) for buffer in buffers]
        for index, (a, b, reference) in enumerate(batches):
            may_be_active = True
            if not ip.read(0) & 4:
                raise RuntimeError('Accelerator not idle before copying')
            may_be_active = False
            if guard:
                for array in full:
                    array[batch_size:].fill(0x12345678)
            start = time.perf_counter()
            arrays[0][:], arrays[1][:] = a, b
            arrays[2].fill(0)
            for buffer in buffers:
                buffer.flush()
            may_be_active = True
            if not ip.read(0) & 4:
                raise RuntimeError('Accelerator not idle before register writes')
            may_be_active = False
            for offset, address in zip((16, 24, 32), pointers):
                ip.write(offset, address)
            ip.write(40, batch_size)
            may_be_active = True
            launch_us = software.bench.board_test._launch_wait(ip, 2.0)
            if not ip.read(0) & 4:
                raise RuntimeError('Done but not idle; retain batch buffers')
            may_be_active = False
            buffers[2].invalidate()
            output = np.array(arrays[2], copy=True)
            elapsed = (time.perf_counter() - start) * 1e6
            if not np.array_equal(output, reference):
                raise AssertionError('Hardware batch output mismatch at ' + str(index))
            if guard:
                buffers[0].invalidate()
                buffers[1].invalidate()
                if any(not np.all(array[batch_size:] == 0x12345678) for array in full):
                    raise AssertionError('Hardware batch tail guard overwritten')
                if not np.array_equal(arrays[0], a) or not np.array_equal(arrays[1], b):
                    raise AssertionError('Hardware batch input overwritten')
            if index >= warmup_matrices // batch_size:
                rows.append({'batch': index - warmup_matrices // batch_size, 'launch_wait_us': launch_us,
                             'end_to_end_us': elapsed, 'per_matrix_us': elapsed / batch_size})
    finally:
        if may_be_active:
            _retained_buffers.append((overlay, buffers))
        else:
            for buffer in buffers:
                buffer.freebuffer()
    return software._result(rows, batch_size, warmup_matrices, participant='hardware_cached' if cacheable else 'hardware_uncached',
                            cacheable=cacheable, guard=guard, launches=len(batches),
                            guard_checks=len(batches) * 3 * 1024 if guard else 0,
                            input_checks=len(batches) * batch_size * 2048 if guard else 0,
                            bit_sha256=manifest['sha256']['autohls_matmul.bit'], clock_mhz=100,
                            firmware_sha256=firmware_sha256,
                            timing_note='One launch per whole batch; copy/prefill/cache/MMIO/poll/output included; allocation/packing/load/checking excluded')
