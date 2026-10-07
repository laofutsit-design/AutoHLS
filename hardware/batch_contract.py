"""Python 3.6-compatible, offline-only checks for the hardware batch interface."""
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

try:
    from .board_test import validate_reset
except ImportError:
    from board_test import validate_reset


def validate_batch_interface(header, hwh):
    for name, offset in (("A", 16), ("B", 24), ("C", 32), ("BATCH_COUNT", 40)):
        for kind, expected in (("ADDR", offset), ("BITS", 32)):
            matches = re.findall(r"#define XMATMUL_AXI_CONTROL_" + kind + "_" + name + r"_DATA\s+(0x[0-9a-fA-F]+|[0-9]+)\s", header)
            if len(matches) != 1 or int(matches[0], 0) != expected:
                raise ValueError("Unexpected batch register: " + name)
    module = hwh.find(".//MODULE[@INSTANCE='matmul_axi_0']")
    if module is None:
        raise ValueError("Missing accelerator module")
    for name in ("C_M_AXI_GMEM_ADDR_WIDTH", "C_M_AXI_GMEM_DATA_WIDTH"):
        param = module.find("./PARAMETERS/PARAMETER[@NAME='" + name + "']")
        if param is None or param.get("VALUE") != "32":
            raise ValueError("Unexpected batch AXI width")
    for name, offset in (("a", 16), ("b", 24), ("c", 32), ("batch_count", 40)):
        register = module.find(".//REGISTER[@NAME='" + name + "']")
        if register is None:
            raise ValueError("Missing HWH batch register: " + name)
        params = {p.get("NAME"): p.get("VALUE") for p in register.findall("./PROPERTY")}
        if int(params.get("ADDRESS_OFFSET", "-1"), 0) != offset or int(params.get("SIZE", "0"), 0) != 32:
            raise ValueError("HWH batch register mismatch: " + name)


def inspect_bundle(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    expected = {'schema_version': 2, 'interface': 'hardware-batch-v1', 'board': 'PYNQ-Z2',
                'part': 'xc7z020clg400-1', 'clock_mhz': 100, 'control_base': 0x43c00000,
                'batch_count_min': 1, 'batch_count_max': 64, 'matrix_stride_bytes': 4096,
                'registers': {'control': 0, 'a': 16, 'b': 24, 'c': 32, 'batch_count': 40}}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError('Unreviewed hardware batch contract')
    if manifest.get('wrapper_rtl_verified') is not True or manifest.get('routed_timing_met') is not True:
        raise ValueError('Offline hardware verification missing')
    for name in ('autohls_matmul.bit', 'autohls_matmul.hwh', 'xmatmul_axi_hw.h'):
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != manifest['sha256'][name]:
            raise ValueError('Checksum mismatch: ' + name)
    hwh = ET.parse(directory / 'autohls_matmul.hwh').getroot()
    validate_reset(hwh)
    validate_batch_interface((directory / 'xmatmul_axi_hw.h').read_text(), hwh)
    address = hwh.find(".//MEMRANGE[@INSTANCE='matmul_axi_0']")
    frequency = hwh.find(".//PARAMETER[@NAME='PCW_FPGA0_PERIPHERAL_FREQMHZ']")
    ddr = hwh.find(".//MODULE[@INSTANCE='matmul_axi_0']//MEMRANGE[@SLAVEBUSINTERFACE='S_AXI_HP0']")
    if (address is None or frequency is None or ddr is None or
            int(address.get('BASEVALUE'), 16) != 0x43c00000 or float(frequency.get('VALUE')) != 100 or
            int(ddr.get('BASEVALUE'), 16) != 0 or int(ddr.get('HIGHVALUE'), 16) != 0x1fffffff):
        raise ValueError('Unreviewed control address, clock or DDR mapping')
    return manifest
