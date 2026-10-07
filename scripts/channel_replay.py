"""Freeze/recompute an application replay. No board connection or synthesized timings."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from autohls import channel_replay as model
from autohls.experiments import file_hash
from hardware.delivery import read, require, verify_files

ROOT = Path(__file__).resolve().parents[1]


def prepare(output):
    require(not output.exists(), 'Replay destination already exists')
    results = {name: model.replay(name) for name in model.SCENARIOS}
    output.mkdir(parents=True)
    for name, value in results.items():
        with (output / (name + '.json')).open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    shutil.copyfile(Path(model.__file__), output / 'channel_replay.py')
    shutil.copyfile(ROOT / 'hardware/channel_replay.py', output / 'board_runner.py')
    manifest = {'schema': model.SCHEMA, 'created_utc': datetime.now(timezone.utc).isoformat(),
                'data_source': 'synthetic_deterministic', 'board_verified': False,
                'files': {p.name: file_hash(p) for p in sorted(output.iterdir())}}
    with (output / 'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, indent=2, allow_nan=False)
    return audit(output)


def audit(root):
    manifest = read(root / 'manifest.json')
    require(manifest['schema'] == model.SCHEMA and manifest['data_source'] == 'synthetic_deterministic'
            and manifest['board_verified'] is False, 'Invalid software replay manifest')
    require(set(manifest['files']) == {'channel_replay.py', 'board_runner.py'} | {n + '.json' for n in model.SCENARIOS},
            'Missing or extra replay inputs')
    verify_files(root, manifest['files'])
    require(file_hash(Path(model.__file__)) == manifest['files']['channel_replay.py'], 'Replay engine changed')
    require(file_hash(ROOT / 'hardware/channel_replay.py') == manifest['files']['board_runner.py'], 'Board runner changed')
    for name in model.SCENARIOS:
        require(read(root / (name + '.json')) == model.replay(name), 'Replay does not recompute: ' + name)
    return {'complete': True, 'software_verified': True, 'board_verified': False,
            'scenarios': 3, 'output_values': 3 * model.SAMPLES * model.CHANNELS,
            'manifest_sha256': file_hash(root / 'manifest.json'), 'cpu_speedup': None}


def board_audit(record, root, receipt_sha256):
    """Read-only validation of a separately acquired, exact-input board record."""
    from hardware.delivery_acceptance import binding
    from hardware.board_benchmark import distribution
    bundle = ROOT / 'artifacts/delivery-upgrade-20260927/v2/exported/artifacts/delivery/bundle'
    receipt = binding(bundle, receipt_sha256)
    from scripts.audit_delivery_board import audit_directory
    prior = audit_directory(ROOT / 'artifacts/delivery-upgrade-20260927/board-v2', receipt_sha256)
    require(record['schema'] == model.SCHEMA and record['receipt_sha256'] == receipt_sha256 and
            record['manifest_sha256'] == file_hash(root / 'manifest.json'), 'Board replay binding mismatch')
    require(record['engine_sha256'] == file_hash(root / 'channel_replay.py'), 'Board replay engine mismatch')
    require(record['runner_sha256'] == file_hash(root / 'board_runner.py'), 'Board replay runner mismatch')
    require(record['firmware_sha256'] == prior['firmware_sha256'], 'Board firmware differs from verified delivery')
    require(set(record['scenarios']) == set(model.SCENARIOS), 'Missing application board scenarios')
    require(set(record['attempts']) == set(model.SCENARIOS), 'Missing application attempts')
    for name in model.SCENARIOS:
        result, software = record['scenarios'][name], read(root / (name + '.json'))
        require(result['passed'] is True and result['scenario'] == name and
                record['attempts'][name] == {'scenario': name, 'input_sha256': software['input_sha256']},
                'Incomplete application attempt')
        require(result['input_sha256'] == software['input_sha256'] and
                result['reference_output_sha256'] == software['output_sha256'], 'Board replay input mismatch')
        measured = result['measurement']
        expected = {'checks': 8192, 'input_checks': 16384, 'guard_checks': 3072,
                    'batch_size': 8, 'measured_batches': 1, 'warmup_batches': 0,
                    'measured_matrices': 8, 'warmup_matrices': 0, 'launches': 1,
                    'cacheable': True, 'guard': True, 'participant': 'hardware_cached',
                    'bit_sha256': receipt['bit_sha256'], 'clock_mhz': 100}
        require(all(measured[key] == value for key, value in expected.items()), 'Board replay measurement mismatch')
        rows = measured['measurements']
        require(len(rows) == 1 and rows[0]['batch'] == 0 and
                rows[0]['per_matrix_us'] == rows[0]['end_to_end_us'] / 8, 'Invalid replay timing row')
        computed = {key: distribution([rows[0][key]])
                    for key in ('launch_wait_us', 'end_to_end_us', 'per_matrix_us')}
        require(computed == measured['statistics_us'], 'Replay statistics mismatch')
        require(measured['firmware_sha256'] == record['firmware_sha256'], 'Firmware changed during replay')
    states = [record['before'], record['final']] + [result[key] for result in record['scenarios'].values()
                                                   for key in ('before', 'after')]
    for state in states:
        require(state['idle'] is True and state['control'] & 4 and not state['control'] & 0x81 and
                state['retained_buffers'] == 0 and state['clock_mhz'] == 100 and
                state['bitfile'] == record['bundle'] + '/autohls_matmul.bit' and
                state['firmware_sha256'] == prior['firmware_sha256'], 'Unsafe application board state')
    require(record['final_idle'] is True and record['retained_buffers'] == 0 and record['cpu_speedup'] is None,
            'Unsafe final state or unmeasured speedup')
    return {'board_verified': True, 'scenarios': 3, 'output_checks': 24576, 'input_checks': 49152,
            'guard_checks': 9216, 'receipt_sha256': receipt_sha256, 'bit_sha256': receipt['bit_sha256'],
            'cpu_speedup': None, 'scope': 'Exact-input functional check; one launch per scenario, not performance evidence'}


def view(root, scenario):
    require(scenario in model.SCENARIOS, 'Unknown replay scenario')
    if not (root / 'manifest.json').is_file():
        return {'available': False}
    checked = audit(root)
    payload = read(root / (scenario + '.json'))
    board = None
    path = root / 'board/record.json'
    if path.exists():
        downloaded = read(root / 'board/download.json')
        require(file_hash(path) == downloaded['downloaded']['sha256'], 'Downloaded board record changed')
        receipt_path = ROOT / 'artifacts/delivery-upgrade-20260927/v2/exported/artifacts/delivery/bundle/delivery.json'
        board = board_audit(read(path), root, file_hash(receipt_path))
    return {'available': True, 'mode': 'read_only_application_replay', 'software_audit': checked,
            'data': payload, 'board': board, 'board_verified': board is not None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'audit', 'audit-board'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action == 'audit-board':
        checked = view(args.directory, 'matched')
        require(checked['board_verified'], 'No application board evidence')
        result = dict(checked['board'], software_audit=checked['software_audit'],
                      record_sha256=file_hash(args.directory / 'board/record.json'))
    else:
        result = prepare(args.directory) if args.action == 'prepare' else audit(args.directory)
    if args.output:
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2))
