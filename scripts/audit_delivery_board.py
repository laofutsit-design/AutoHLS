"""Independently check archived board acceptance; never connect to hardware."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

from autohls.experiments import file_hash
from hardware import delivery_acceptance as runner
from hardware.delivery import member, read, require, verify_files


def archive_files(archive, expected_sha256):
    require(file_hash(archive) == expected_sha256, 'Board archive checksum mismatch')
    with zipfile.ZipFile(archive) as package:
        checksums = json.loads(package.read('board-evidence-checksums.json'))
        names = package.namelist()
        require(len(names) == len(checksums) + 1 and
                set(names) == set(checksums) | {'board-evidence-checksums.json'},
                'Unexpected or duplicate board archive members')
        for name, checksum in checksums.items():
            target = member(Path('archive-validation'), name)
            require(name == PurePosixPath(name).as_posix() and not name.endswith('/') and
                    target.name not in ('', '.'), 'Invalid board archive member')
            require(hashlib.sha256(package.read(name)).hexdigest() == checksum,
                    'Board archive member checksum mismatch: ' + name)
        checksums['board-evidence-checksums.json'] = hashlib.sha256(
            package.read('board-evidence-checksums.json')).hexdigest()
    return checksums


def extract(archive, destination, expected_sha256):
    checksums = archive_files(archive, expected_sha256)
    destination.mkdir(parents=True, exist_ok=False)
    # Write ordinary files, not archive-provided links or filesystem attributes.
    with zipfile.ZipFile(archive) as package:
        for name in checksums:
            target = member(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as handle:
                handle.write(package.read(name))
    return len(checksums)


def audit(root, archive, archive_sha256, receipt_sha256, runtime_manifest_sha256):
    checksums = archive_files(archive, archive_sha256)
    require({p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()} == set(checksums),
            'Missing or extra board evidence files')
    count = verify_files(root, checksums)
    require(file_hash(root / 'runtime-manifest.json') == runtime_manifest_sha256,
            'Runtime differs from the prepared upload')
    runtime = read(root / 'runtime-manifest.json')
    require(runtime['receipt_sha256'] == receipt_sha256 and runtime['board_verified'] is False,
            'Runtime receipt mismatch')
    verify_files(root, runtime['files'])
    require(file_hash(Path(runner.__file__)) == runtime['runner_sha256'] ==
            file_hash(root / 'hardware/delivery_acceptance.py'), 'Frozen acceptance runner mismatch')
    evidence = root / 'acceptance'
    summary = runner.audit(evidence, root / 'bundle', receipt_sha256)
    require(summary == read(evidence / 'summary.json'), 'Board summary differs from recomputed samples')
    require(summary['bit_sha256'] == runtime['bit_sha256'], 'Runtime bit mismatch')
    verification = read(evidence / 'runtime-verification.json')
    require(verification['runtime_manifest_sha256'] == runtime_manifest_sha256 and
            verification['receipt_sha256'] == receipt_sha256 and verification['programmed'] is False and
            verification['members'] == len(runtime['files']) + 1, 'Runtime verification mismatch')
    preflight = read(evidence / 'preflight.json')
    require(preflight['pynq'] == '2.5' and preflight['diag_active'] is False and
            preflight['diag_buffer_count'] == 0 and not any(preflight['retained_buffers'].values()) and
            preflight['new_runtime_exists'] is False and preflight['programmed'] is False,
            'Preflight was not safe')
    load = read(evidence / 'load-proof.json')
    require(load['loaded'] is True and load['receipt_sha256'] == receipt_sha256 and
            load['bit_sha256'] == summary['bit_sha256'], 'Load receipt mismatch')
    plan = read(evidence / 'protocol.json')
    bitfile = str(PurePosixPath(plan['bundle']) / 'autohls_matmul.bit')
    require(load['bitfile'] == bitfile, 'Loaded path differs from protocol')
    for name in ('idle-before.json', 'final-state.json'):
        state = read(evidence / name)
        require(state['idle'] is True and state['control'] & 4 and not state['control'] & 0x81 and
                state['retained_buffers'] == 0, 'Accelerator not safely idle: ' + name)
        require(state['clock_mhz'] == load['clock_mhz'] == 100 and
                state['firmware_sha256'] == load['firmware_sha256'] == summary['firmware_sha256'],
                'Clock or staged firmware changed')
    final = read(evidence / 'final-state.json')
    require(final['bitfile'] == bitfile, 'Final loaded path mismatch')
    trace = [json.loads(line) for line in (evidence / 'load.jsonl').read_text().splitlines()]
    events = [row['event'] for row in trace]
    require(events[0] == 'load_begin' and events[-1] == 'load_complete' and
            events.count('load_begin') == events.count('load_complete') == 1 and
            set(events) <= {'load_begin', 'call', 'line', 'return', 'load_complete'},
            'Load trace incomplete, failed or repeated')
    times = [row['monotonic'] for row in trace]
    require(all(a < b for a, b in zip(times, times[1:])), 'Load trace order invalid')
    require(any(row.get('function') == 'pynq.pl_server.device.download' and row['event'] == 'return'
                for row in trace), 'Missing completed device download')
    return dict(summary, verified_files=count, archive_sha256=archive_sha256,
                runtime_manifest_sha256=runtime_manifest_sha256, final_idle=True,
                retained_buffers=0, clock_mhz=100,
                firmware_check='Fresh download trace and staged file hash, not physical FPGA readback')


def audit_directory(directory, receipt_sha256):
    return audit(directory / 'results', directory / 'delivery-board-evidence.zip',
                 read(directory / 'download.json')['downloaded']['sha256'], receipt_sha256,
                 file_hash(directory / 'runtime/runtime-manifest.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('receipt_sha256')
    parser.add_argument('output', type=Path)
    parser.add_argument('--extract', action='store_true')
    args = parser.parse_args()
    require(not args.output.exists(), 'Audit output already exists')
    if args.extract:
        extract(args.directory / 'delivery-board-evidence.zip', args.directory / 'results',
                read(args.directory / 'download.json')['downloaded']['sha256'])
    result = audit_directory(args.directory, args.receipt_sha256)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2))
