"""One-attempt application checks with the existing overlay; Python 3.6."""
import importlib.util
import sys
from pathlib import Path

from hardware import delivery_acceptance as acceptance


def state(overlay):
    import pynq
    retained = sum(len(module._retained_buffers) for module in list(sys.modules.values())
                   if hasattr(module, '_retained_buffers'))
    control = int(overlay.matmul_axi_0.read(0))
    acceptance.require(retained == 0 and control & 4 and not control & 0x81,
                       'Board not safely idle; no reset or retry')
    acceptance.require(overlay.is_loaded() and pynq.PL.bitfile_name == overlay.bitfile_name,
                       'Overlay changed')
    acceptance.require(abs(float(pynq.Clocks.fclk0_mhz) - 100) < .1, 'Clock changed')
    return {'idle': True, 'control': control, 'retained_buffers': retained,
            'clock_mhz': float(pynq.Clocks.fclk0_mhz), 'bitfile': overlay.bitfile_name,
            'firmware_sha256': acceptance.driver.verify_firmware(overlay)}


def start(software, output, bundle, receipt_sha256, overlay):
    software, output = Path(software), Path(output)
    acceptance.binding(bundle, receipt_sha256)
    manifest = acceptance.read(software / 'manifest.json')
    for name, checksum in manifest['files'].items():
        acceptance.require(Path(name).name == name, 'Invalid input path')
        acceptance.require(acceptance.digest(software / name) == checksum, 'Software input changed')
    acceptance.require(acceptance.digest(__file__) == manifest['files']['board_runner.py'], 'Runner changed')
    before = state(overlay)
    output.mkdir(exist_ok=False)
    plan = {'schema': 'channel-calibration-replay-v1', 'software': str(software), 'bundle': str(bundle),
            'receipt_sha256': receipt_sha256, 'manifest_sha256': acceptance.digest(software / 'manifest.json'),
            'engine_sha256': manifest['files']['channel_replay.py'],
            'runner_sha256': manifest['files']['board_runner.py'], 'before': before,
            'scenarios': ['matched', 'bypass', 'drift']}
    acceptance.save(output / 'protocol.json', plan)
    return plan


def run_step(output, overlay, index, confirm_board=False):
    acceptance.require(confirm_board is True, 'Exclusive board use required')
    output = Path(output)
    plan = acceptance.read(output / 'protocol.json')
    acceptance.require(type(index) is int and 0 <= index < 3, 'Invalid scenario index')
    acceptance.require(plan['scenarios'] == ['matched', 'bypass', 'drift'], 'Protocol changed')
    for previous in range(index):
        acceptance.require(acceptance.read(output / ('result-%d.json' % previous))['passed'] is True,
                           'Earlier scenario not complete')
    software = Path(plan['software'])
    acceptance.require(acceptance.digest(software / 'manifest.json') == plan['manifest_sha256'], 'Manifest changed')
    acceptance.require(acceptance.digest(software / 'channel_replay.py') == plan['engine_sha256'], 'Engine changed')
    acceptance.require(acceptance.digest(__file__) == plan['runner_sha256'], 'Runner changed')
    acceptance.binding(plan['bundle'], plan['receipt_sha256'])
    before = state(overlay)
    acceptance.require(before['firmware_sha256'] == plan['before']['firmware_sha256'], 'Firmware changed')
    name = plan['scenarios'][index]
    spec = importlib.util.spec_from_file_location('frozen_channel_replay', str(software / 'channel_replay.py'))
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    reference = model.replay(name)
    acceptance.require(reference == acceptance.read(software / (name + '.json')), 'Software does not recompute')
    values = model.board_values(name)
    # This file is an exclusive attempt marker. Never retry a failed/unknown run.
    acceptance.save(output / ('attempt-%d.json' % index),
                    {'scenario': name, 'input_sha256': reference['input_sha256']})
    try:
        measurement = acceptance.driver.run_fpga(plan['bundle'], overlay, values, 8,
            warmup_matrices=0, confirm_board=True, cacheable=True, guard=True)
        after = state(overlay)
        result = {'passed': True, 'scenario': name, 'input_sha256': reference['input_sha256'],
                  'reference_output_sha256': reference['output_sha256'], 'measurement': measurement,
                  'before': before, 'after': after}
        acceptance.save(output / ('result-%d.json' % index), result)
    except BaseException as error:
        acceptance.save(output / ('failure-%d.json' % index),
                        {'error': type(error).__name__ + ': ' + str(error), 'retry_allowed': False})
        raise
    return {'scenario': name, 'passed': True, 'output_checks': measurement['checks']}


def collect(output, overlay):
    output = Path(output)
    plan = acceptance.read(output / 'protocol.json')
    acceptance.require(not list(output.glob('failure-*.json')), 'Failed attempt exists')
    results, attempts = {}, {}
    for index, name in enumerate(plan['scenarios']):
        results[name] = acceptance.read(output / ('result-%d.json' % index))
        attempts[name] = acceptance.read(output / ('attempt-%d.json' % index))
    final = state(overlay)
    record = dict(plan, scenarios=results, attempts=attempts, final=final, final_idle=True,
                  retained_buffers=0, firmware_sha256=final['firmware_sha256'], cpu_speedup=None)
    acceptance.save(output / 'record.json', record)
    return {'path': str(output / 'record.json'), 'sha256': acceptance.digest(output / 'record.json')}
