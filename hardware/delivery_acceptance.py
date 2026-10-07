"""Receipt-bound, one-attempt board acceptance. Python 3.6; no overlay loads.

Call start offline, explicitly load a fresh reviewed Overlay in a separate
confirmed step, then call run_step once per index. Never retry an attempted step.
"""
import hashlib
import json
from pathlib import Path

from . import board_hardware_batch as driver
from . import batch_contract


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=True, indent=2, allow_nan=False)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def binding(bundle, expected_receipt):
    bundle = Path(bundle).resolve()
    require(digest(bundle / 'delivery.json') == expected_receipt, 'Delivery receipt changed')
    checksums = read(bundle / 'checksums.json')
    require(bool(checksums), 'Missing bundle checksums')
    for name, expected in checksums.items():
        require(Path(name).name == name and '/' not in name and '\\' not in name and ':' not in name,
                'Invalid bundle member')
        require(digest(bundle / name) == expected, 'Bundle checksum mismatch: ' + name)
    manifest = batch_contract.inspect_bundle(bundle)
    receipt = read(bundle / 'delivery.json')
    require(receipt['contract'] == 'matmul32-batch64-v1' and receipt['board_verified'] is False and
            receipt['state'] == 'awaiting_board_confirmation', 'Unreviewed delivery state')
    for key, expected in (('bit_sha256', manifest['sha256']['autohls_matmul.bit']),
                          ('hwh_sha256', manifest['sha256']['autohls_matmul.hwh']),
                          ('source_sha256', digest(bundle / 'kernel.cpp')),
                          ('manifest_sha256', digest(bundle / 'manifest.json')),
                          ('provenance_sha256', digest(bundle / 'provenance.json')),
                          ('build_code_sha256', digest(bundle / 'source-checksums.json'))):
        require(receipt[key] == expected, 'Receipt binding mismatch: ' + key)
    # Pin the actual imported runtime to the build's frozen board driver modules.
    frozen = read(bundle / 'source-checksums.json')
    modules = (driver, driver.software, driver.software.bench, driver.software.bench.board_test, batch_contract)
    for module in modules:
        require(digest(module.__file__) == frozen['hardware/' + Path(module.__file__).name],
                'Board driver differs from frozen build')
    return receipt


def steps():
    return [{'index': index, 'batch_size': count, 'cacheable': cached, 'seed': 20260927 + count,
              'samples': 2 * count, 'warmup_matrices': 0, 'guard': True}
             for index, (count, cached) in enumerate((count, cached)
                 for count in (1, 2, 3, 4, 16, 63, 64) for cached in (False, True))]


def start(bundle, receipt_sha256, output):
    receipt = binding(bundle, receipt_sha256)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    plan = {'schema_version': 1, 'contract': 'delivery-functional-acceptance-v1',
            'bundle': str(Path(bundle).resolve()), 'receipt_sha256': receipt_sha256,
            'bit_sha256': receipt['bit_sha256'], 'runner_sha256': digest(__file__), 'steps': steps(),
            'scope': 'Functional guarded acceptance only; not a CPU comparison or application benchmark'}
    save(output / 'protocol.json', plan)
    return plan


def run_step(output, overlay, index, confirm_board=False):
    if confirm_board is not True:
        raise RuntimeError('Current exclusive board use must be explicitly confirmed')
    output = Path(output)
    plan = read(output / 'protocol.json')
    require(plan['runner_sha256'] == digest(__file__), 'Acceptance runner changed')
    require(plan['steps'] == steps(), 'Acceptance steps changed')
    require(type(index) is int and 0 <= index < len(plan['steps']), 'Invalid step index')
    for previous in range(index):
        require(read(output / ('result-%02d.json' % previous))['status'] == 'passed',
                'Earlier step is not complete')
    binding(plan['bundle'], plan['receipt_sha256'])
    step = plan['steps'][index]
    # Create the attempt marker BEFORE hardware access. A crash is not retryable.
    save(output / ('attempt-%02d.json' % index), {'step': step, 'receipt_sha256': plan['receipt_sha256']})
    try:
        values, input_hash = driver.software.bench.dataset(step['seed'], step['samples'])
        measured = driver.run_fpga(plan['bundle'], overlay, values, step['batch_size'],
                                  warmup_matrices=0, confirm_board=True, cacheable=step['cacheable'], guard=True)
        result = {'status': 'passed', 'step': step, 'receipt_sha256': plan['receipt_sha256'],
                  'dataset_sha256': input_hash, 'result': measured}
    except BaseException as error:
        save(output / ('failure-%02d.json' % index), {'status': 'failed', 'step': step,
             'error': type(error).__name__ + ': ' + str(error), 'retry_allowed': False})
        raise
    save(output / ('result-%02d.json' % index), result)
    return {'index': index, 'status': 'passed', 'bit_sha256': measured['bit_sha256']}


def audit(output, bundle, receipt_sha256):
    output = Path(output)
    receipt = binding(bundle, receipt_sha256)
    plan = read(output / 'protocol.json')
    require(plan['contract'] == 'delivery-functional-acceptance-v1' and
            plan['receipt_sha256'] == receipt_sha256 and plan['bit_sha256'] == receipt['bit_sha256'] and
            plan['runner_sha256'] == digest(__file__), 'Acceptance protocol binding changed')
    expected = [(count, cached) for count in (1, 2, 3, 4, 16, 63, 64) for cached in (False, True)]
    require(len(plan['steps']) == len(expected), 'Missing planned steps')
    for prefix in ('attempt', 'result'):
        require({p.name for p in output.glob(prefix + '-*.json')} ==
                {prefix + '-%02d.json' % index for index in range(14)}, 'Missing or extra step files')
    require(not list(output.glob('failure-*.json')), 'Failed acceptance must be retained, not relabelled')
    checks, guards, inputs, firmware = 0, 0, 0, set()
    for index, (count, cached) in enumerate(expected):
        step = plan['steps'][index]
        require(step == {'index': index, 'batch_size': count, 'cacheable': cached, 'seed': 20260927 + count,
                         'samples': 2 * count, 'warmup_matrices': 0, 'guard': True}, 'Step protocol changed')
        attempt = read(output / ('attempt-%02d.json' % index))
        record = read(output / ('result-%02d.json' % index))
        require(attempt == {'step': step, 'receipt_sha256': receipt_sha256} and record['step'] == step and
                record['status'] == 'passed' and record['receipt_sha256'] == receipt_sha256, 'Step binding mismatch')
        _, input_hash = driver.software.bench.dataset(step['seed'], step['samples'])
        require(record['dataset_sha256'] == input_hash, 'Dataset mismatch')
        value = record['result']
        for key, expected_value in (('bit_sha256', receipt['bit_sha256']), ('batch_size', count),
                ('cacheable', cached), ('guard', True), ('clock_mhz', 100), ('launches', 2),
                ('measured_batches', 2), ('warmup_batches', 0), ('measured_matrices', 2 * count),
                ('warmup_matrices', 0), ('checks', 2 * count * 1024), ('guard_checks', 6 * 1024),
                ('input_checks', 2 * count * 2048), ('participant', 'hardware_cached' if cached else 'hardware_uncached')):
            require(value[key] == expected_value, 'Acceptance result mismatch: ' + key)
        rows = value['measurements']
        require(len(rows) == 2 and [row['batch'] for row in rows] == [0, 1], 'Missing raw samples')
        require(all(row['per_matrix_us'] == row['end_to_end_us'] / count for row in rows), 'Wrong timing units')
        computed = {key: driver.software.bench.distribution([row[key] for row in rows])
                    for key in ('launch_wait_us', 'end_to_end_us', 'per_matrix_us')}
        require(computed == value['statistics_us'], 'Statistics do not match raw samples')
        firmware.add(value['firmware_sha256'])
        checks += value['checks']
        guards += value['guard_checks']
        inputs += value['input_checks']
    require(len(firmware) == 1, 'Firmware changed during acceptance')
    return {'complete': True, 'board_verified': True, 'receipt_sha256': receipt_sha256,
            'bit_sha256': receipt['bit_sha256'], 'steps': 14, 'output_checks': checks,
            'guard_checks': guards, 'input_checks': inputs, 'firmware_sha256': next(iter(firmware)),
            'scope': plan['scope'], 'cpu_speedup': None}
