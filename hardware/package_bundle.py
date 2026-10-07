"""Collect verified cloud build outputs; never communicates with a board."""
import json
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET

from autohls.experiments import file_hash, write_json
from autohls.vitis import parse_csynth_xml
from hardware.board_test import validate_reset


ROOT = Path(__file__).resolve().parents[1]


def collect(work, destination, hardware_batch=False):
    log = (work / "vivado-build.log").read_text()
    if "AUTOHLS_PREBOARD_BUILD_COMPLETE; board_verified=false" not in log:
        raise ValueError("Incomplete routed build: " + str(work))
    timing = (work / "timing_summary.rpt").read_text()
    if "All user specified timing constraints are met." not in timing:
        raise ValueError("Timing constraints not met")
    check = (work / "check_timing.rpt").read_text()
    counts = re.findall(r"There are (\d+) ", check)
    if len(counts) < 12 or any(int(count) for count in counts):
        raise ValueError("Unreviewed clock, constraint or timing-check warnings")
    drc = (work / "drc.rpt").read_text()
    if re.search(r"\|\s*(?:Critical Warning|Error)\s*\|", drc):
        raise ValueError("DRC errors or critical warnings")
    hls = work / "axi_project/solution1"
    cosim = hls / "sim/report/matmul_axi_cosim.rpt"
    if not re.search(r"\|\s*Verilog\s*\|\s*Pass\s*\|", cosim.read_text()):
        raise ValueError("Missing AXI wrapper RTL pass")
    if "C/RTL co-simulation finished: PASS" not in (work / "hls.log").read_text():
        raise ValueError("Missing wrapper simulation completion")
    headers = list((hls / "impl/ip/drivers").rglob("xmatmul_axi_hw.h"))
    if len(headers) != 1:
        raise ValueError("Ambiguous register header")
    header = headers[0].read_text()
    registers = {}
    ports = [("control", "AP_CTRL"), ("a", "A_DATA"), ("b", "B_DATA"), ("c", "C_DATA")]
    expected = {"control": 0, "a": 16, "b": 24, "c": 32}
    if hardware_batch:
        ports.append(("batch_count", "BATCH_COUNT_DATA"))
        expected["batch_count"] = 40
    elif "XMATMUL_AXI_CONTROL_ADDR_BATCH_COUNT_DATA" in header:
        raise ValueError("Batch hardware cannot use the single-matrix bundle contract")
    for name, macro in ports:
        registers[name] = int(re.search(r"#define XMATMUL_AXI_CONTROL_ADDR_" + macro + r"\s+(0x[0-9a-fA-F]+)", header)[1], 16)
    if registers != expected:
        raise ValueError("Driver register layout changed")
    hwh = ET.parse(work / "bundle/autohls_matmul.hwh").getroot()
    validate_reset(hwh)
    if hardware_batch:
        from hardware.package_batch import validate_batch_interface
        validate_batch_interface(header, hwh)
    address = hwh.find(".//MEMRANGE[@INSTANCE='matmul_axi_0']")
    frequency = hwh.find(".//PARAMETER[@NAME='PCW_FPGA0_PERIPHERAL_FREQMHZ']")
    if int(address.attrib["BASEVALUE"], 16) != 0x43C00000 or float(frequency.attrib["VALUE"]) != 100:
        raise ValueError("Unexpected AXI address or clock")
    destination.mkdir()
    files = {"autohls_matmul.bit": work / "bundle/autohls_matmul.bit",
             "autohls_matmul.hwh": work / "bundle/autohls_matmul.hwh",
             "xmatmul_axi_hw.h": headers[0], "kernel.cpp": work / "kernel.cpp",
             "matmul_axi.cpp": ROOT / "hardware/hls" / ("matmul_batch_axi.cpp" if hardware_batch else "matmul_axi.cpp"),
             "matmul_axi_csynth.xml": hls / "syn/report/matmul_axi_csynth.xml",
             "matmul_axi_csynth.rpt": hls / "syn/report/matmul_axi_csynth.rpt",
             "matmul_axi_cosim.rpt": cosim,
             "revision-workaround.json": hls / "impl/ip/revision-workaround.json"}
    files.update({name: work / name for name in ("timing_summary.rpt", "check_timing.rpt", "utilization.rpt", "drc.rpt")})
    for name, source in files.items():
        shutil.copyfile(source, destination / name)
    manifest = {
        "schema_version": 2 if hardware_batch else 1, "variant": work.name, "board": "PYNQ-Z2", "part": "xc7z020clg400-1",
        "tool": "Vivado / Vivado HLS 2019.1 WebPACK", "clock_mhz": 100,
        "board_verified": False, "wrapper_rtl_verified": True, "routed_timing_met": True,
        "control_base": int(address.attrib["BASEVALUE"], 16), "registers": registers,
        "input_contract": "Two row-major 32x32 int32 DDR arrays, values [-1000,1000]; int16 internal multiplication; int32 result",
        "setup_slack_ns": float(re.search(r"AUTOHLS_ROUTED_SETUP_SLACK_NS=([^\s]+)", log)[1]),
        "hold_slack_ns": float(re.search(r"AUTOHLS_ROUTED_HOLD_SLACK_NS=([^\s]+)", log)[1]),
        "hls_wrapper_metrics": parse_csynth_xml(hls / "syn/report/matmul_axi_csynth.xml", target_clock_ns=10),
        "drc_warnings": [{"rule": rule, "count": int(count)} for rule, count in
                         re.findall(r"\|\s*([A-Z0-9]+-\d+)\s*\|\s*Warning\s*\|[^|]*\|\s*(\d+)\s*\|", drc)],
        "warning_note": "Vendor AXI FIFO async-reset/RAMB warnings retained. Do not reset or reload overlay during a transaction. Board acceptance is still required.",
        "source_build_directory": str(work.resolve()),
        "sha256": {name: file_hash(destination / name) for name in files},
    }
    if hardware_batch:
        manifest.update(interface="hardware-batch-v1", batch_count_min=1, batch_count_max=64,
                        matrix_stride_bytes=4096, invalid_count_behavior="return without DDR access",
                        input_contract="Two contiguous arrays of batch_count row-major 32x32 int32 matrices; values [-1000,1000]; int16 internal multiplication; int32 result")
    write_json(destination / "manifest.json", manifest)
    return manifest


def main():
    builds, output = map(Path, sys.argv[1:])
    output.mkdir(parents=True, exist_ok=False)
    manifests = [collect(builds / variant, output / variant) for variant in ("baseline32", "optimized32")]
    for name in ("board_test.py", "02_matmul_acceptance.ipynb", "README_BOARD.md"):
        shutil.copyfile(ROOT / "hardware" / name, output / name)
    write_json(output / "bundle-summary.json", {"variants": manifests, "board_verified": False})
    write_json(output / "checksums.json", {str(path.relative_to(output)): file_hash(path)
               for path in output.rglob("*") if path.is_file()})
    print("Prepared offline bundle:", output)


if __name__ == "__main__":
    main()
