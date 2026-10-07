"""Independently reparse delivered reports; never runs synthesis or a board."""
import json
from pathlib import Path
import re
import sys

from autohls.experiments import file_hash
from autohls.vitis import parse_csynth_xml
from hardware.delivery import read, require, verify_delivery, verify_files
from hardware.package_ip import project_revision


def reports(bundle):
    manifest = read(bundle / 'manifest.json')
    native = (bundle / 'native-check.log').read_text()
    require(re.search(r'^PASS cases=11 checks=720896 seed=20260926$', native, re.M) and
            not re.search(r'FAIL|ERROR|runtime error|AddressSanitizer', native), 'Native report rejected')
    hls = (bundle / 'hls.log').read_text()
    require('CSim done with 0 errors' in hls and 'C/RTL co-simulation finished: PASS' in hls and
            len(re.findall(r'^PASS cases=11 checks=720896 seed=20260926$', hls, re.M)) >= 2 and
            not re.search(r'^FAIL\b', hls, re.M), 'C/RTL post-check rejected')
    require(re.search(r'\|\s*Verilog\s*\|\s*Pass\s*\|', (bundle / 'matmul_axi_cosim.rpt').read_text()),
            'Missing Verilog pass table')
    # The original 2019.1 export failure is expected and retained, not erased.
    errors = re.findall(r'^ERROR:.*$', hls, re.M)
    require(errors == ['ERROR: [IMPL 213-28] Failed to generate IP.'] and
            'set_property core_revision' in hls, 'Unreviewed HLS error')
    require(parse_csynth_xml(bundle / 'matmul_axi_csynth.xml', 10) == manifest['hls_wrapper_metrics'],
            'HLS metric mismatch')
    timing = (bundle / 'timing_summary.rpt').read_text()
    require('All user specified timing constraints are met.' in timing, 'Routed timing rejected')
    counts = re.findall(r'There are (\d+) ', (bundle / 'check_timing.rpt').read_text())
    require(len(counts) >= 12 and all(int(count) == 0 for count in counts), 'Unreviewed timing checks')
    log = (bundle / 'vivado-build.log').read_text()
    require('AUTOHLS_PREBOARD_BUILD_COMPLETE; board_verified=false' in log, 'Incomplete routed build')
    for kind, key in (('SETUP', 'setup_slack_ns'), ('HOLD', 'hold_slack_ns')):
        values = re.findall('AUTOHLS_ROUTED_' + kind + r'_SLACK_NS=([^\s]+)', log)
        require(len(values) == 1 and float(values[0]) >= 0 and float(values[0]) == manifest[key],
                'Routed slack mismatch')
    drc = (bundle / 'drc.rpt').read_text()
    require(not re.search(r'\|\s*(?:Critical Warning|Error)\s*\|', drc), 'DRC rejected')
    warnings = [{'rule': rule, 'count': int(count)} for rule, count in re.findall(
        r'\|\s*([A-Z0-9]+-\d+)\s*\|\s*Warning\s*\|[^|]*\|\s*(\d+)\s*\|', drc)]
    require(warnings == manifest['drc_warnings'], 'DRC warning omission')
    return {'native_checks': 720896, 'wrapper_rtl_passed': True, 'routed_timing_met': True,
            'setup_slack_ns': manifest['setup_slack_ns'], 'hold_slack_ns': manifest['hold_slack_ns'],
            'drc_warnings': warnings, 'retained_hls_export_errors': errors}


def audit(root):
    count = verify_files(root, read(root / 'export-checksums.json'))
    receipt = verify_delivery(root)
    bundle = root / 'artifacts/delivery/bundle'
    result = reports(bundle)
    ip = root / 'artifacts/delivery/build/axi_project/solution1/impl/ip'
    original = (ip / 'run_ippack.tcl').read_text()
    patched, revision = project_revision(original)
    require((ip / 'run_ippack_autohls.tcl').read_text() == patched, 'Unreviewed package workaround')
    workaround = read(bundle / 'revision-workaround.json')
    require(workaround['old_revision'] == revision and workaround['new_revision'] == 1 and
            file_hash(ip / 'run_ippack.tcl') == workaround['original_sha256'] and
            file_hash(ip / 'run_ippack_autohls.tcl') == workaround['patched_sha256'], 'Workaround hashes changed')
    result.update(verified_files=count, receipt_sha256=file_hash(bundle / 'delivery.json'),
                  bit_sha256=receipt['bit_sha256'], complete=True, board_verified=False,
                  scope='Offline search-to-firmware verification; board acceptance is separate')
    return result


if __name__ == '__main__':
    result = audit(Path(sys.argv[1]).resolve())
    with Path(sys.argv[2]).open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2))
