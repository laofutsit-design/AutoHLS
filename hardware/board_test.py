"""PYNQ 2.5 acceptance helper. Import/inspect never programs hardware.

Only run_tests(..., confirm_board=True) accesses the physical board.
Python 3.6 compatible; copy this file with the two verified overlay bundles.
"""
import hashlib
import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET


# Keep CMA allocations alive if an interrupted/timed-out AXI master may use them.
_retained_buffers = []


def validate_reset(hwh):
    """Reject the reproduced stuck-reset wiring before any hardware access."""
    reset = hwh.find(".//MODULE[@INSTANCE='rst']")
    if reset is None:
        raise ValueError("Missing reviewed reset controller")
    params = {p.attrib["NAME"]: p.attrib["VALUE"] for p in reset.findall("./PARAMETERS/PARAMETER")}
    if params.get("C_EXT_RESET_HIGH") != "0" or params.get("C_AUX_RESET_HIGH") != "1":
        raise ValueError("Unsafe auxiliary reset polarity; do not load this overlay")
    aux = reset.find("./PORTS/PORT[@NAME='aux_reset_in']")
    if aux is not None and aux.get("SIGNAME"):
        for constant in hwh.findall(".//MODULE[@MODTYPE='xlconstant']"):
            output = constant.find("./PORTS/PORT[@NAME='dout']")
            value = constant.find("./PARAMETERS/PARAMETER[@NAME='CONST_VAL']")
            if output is not None and value is not None and output.get("SIGNAME") == aux.get("SIGNAME"):
                if int(value.attrib["VALUE"], 0) == 0:
                    return
    raise ValueError("Auxiliary reset must be tied to the reviewed inactive level")


def inspect_bundle(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["board"] != "PYNQ-Z2" or manifest["part"] != "xc7z020clg400-1":
        raise ValueError("This helper only accepts the reviewed PYNQ-Z2 target")
    if manifest["registers"] != {"control": 0, "a": 16, "b": 24, "c": 32}:
        raise ValueError("Unreviewed register layout")
    for name in ("autohls_matmul.bit", "autohls_matmul.hwh", "xmatmul_axi_hw.h"):
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if digest != manifest["sha256"][name]:
            raise ValueError("Checksum mismatch: " + name)
    validate_reset(ET.parse(directory / "autohls_matmul.hwh").getroot())
    return manifest


def _launch_wait(ip, timeout_s, clock=time.monotonic):
    if not ip.read(0) & 4:
        raise RuntimeError("Accelerator is not idle; do not overwrite its buffers")
    start = clock()
    ip.write(0, 1)
    while not ip.read(0) & 2:
        if clock() - start > timeout_s:
            raise TimeoutError("AXI accelerator timed out; stop and retain the notebook kernel")
    return (clock() - start) * 1e6


def run_tests(directory, confirm_board=False, timeout_s=2.0):
    """Program one overlay and check 20 deterministic cases; explicit opt-in only.

    Timing excludes allocation/programming/reference calculation. launch_wait_us
    includes MMIO and Python polling; end_to_end_us also includes buffer copies,
    cache maintenance and output copy. Neither is a pure hardware cycle counter.
    """
    if confirm_board is not True:
        raise RuntimeError("Board use not confirmed; use inspect_bundle() for offline checks")
    if _retained_buffers:
        raise RuntimeError("Previous operation may still be active. Do not rerun; recover the board first.")
    if not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be positive and finite")
    manifest = inspect_bundle(directory)
    import numpy as np
    import pynq
    from pynq import Clocks, Overlay, allocate
    if not str(pynq.__version__).startswith("2.5"):
        raise RuntimeError("This acceptance helper targets PYNQ 2.5")
    overlay = Overlay(str(Path(directory) / "autohls_matmul.bit"))
    ip = overlay.matmul_axi_0
    if int(overlay.ip_dict["matmul_axi_0"]["phys_addr"]) != manifest["control_base"]:
        raise RuntimeError("Unexpected control address")
    actual_clock = float(Clocks.fclk0_mhz)
    if abs(actual_clock - manifest["clock_mhz"]) > 0.1:
        raise RuntimeError("Unexpected FCLK0 frequency: " + str(actual_clock))
    buffers = []
    may_be_active = False
    measurements = []
    rng = np.random.RandomState(20260916)
    try:
        for _ in range(3):
            buffers.append(allocate(shape=(32, 32), dtype=np.int32))
        a_buf, b_buf, c_buf = buffers
        for buffer in buffers:
            address = int(buffer.physical_address)
            if address % 4 or address < 0x00100000 or address + buffer.nbytes > 0x20000000:
                raise ValueError("Buffer is outside the reviewed PS DDR address map")
        for case in range(20):
            a = rng.randint(-1000, 1001, size=(32, 32)).astype(np.int32)
            b = rng.randint(-1000, 1001, size=(32, 32)).astype(np.int32)
            if case == 0:
                a.fill(0)
            elif case == 1:
                a.fill(1000)
                b.fill(1000)
            elif case == 2:
                a.fill(-1000)
                b.fill(1000)
            elif case == 3:
                b = np.eye(32, dtype=np.int32)
            reference = np.dot(a.astype(np.int64), b.astype(np.int64))
            start = time.monotonic()
            if not ip.read(0) & 4:
                may_be_active = True
                raise RuntimeError("Accelerator was already active")
            a_buf[:] = a
            b_buf[:] = b
            c_buf.fill(0)
            for port, buffer in zip(("a", "b", "c"), buffers):
                buffer.flush()
                ip.write(manifest["registers"][port], int(buffer.physical_address))
            # Set before the start write: an interrupted MMIO call is ambiguous.
            may_be_active = True
            launch_wait_us = _launch_wait(ip, timeout_s)
            may_be_active = False
            c_buf.invalidate()
            result = np.array(c_buf, copy=True)
            end_to_end_us = (time.monotonic() - start) * 1e6
            if not np.array_equal(result, reference):
                raise AssertionError("FPGA output mismatch in case " + str(case))
            measurements.append({"case": case, "launch_wait_us": launch_wait_us,
                                 "end_to_end_us": end_to_end_us})
    finally:
        if may_be_active:
            _retained_buffers.append((overlay, buffers))
        else:
            for buffer in buffers:
                buffer.freebuffer()
    return {"variant": manifest["variant"], "board_verified": True, "cases": 20,
            "checks": 20480, "seed": 20260916, "clock_mhz": actual_clock,
            "pynq_version": pynq.__version__, "bit_sha256": manifest["sha256"]["autohls_matmul.bit"],
            "timing_note": "Python launch/poll and buffer-inclusive measurements, not pure kernel cycles",
            "measurements": measurements}
