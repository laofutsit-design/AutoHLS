"""Deterministic 32-channel calibration demonstration; Python 3.6 compatible.

Synthetic replay, not acquired sensor data. Hand-specified Q8 coefficients,
not model-learned calibration. This module never imports PYNQ or accesses PL.
"""
import hashlib
import json
import math
import struct

SCHEMA = 'channel-calibration-replay-v1'
SCENARIOS = ('matched', 'bypass', 'drift')
CHANNELS, SAMPLES, SCALE = 32, 256, 256


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()


def matrices(scenario):
    if scenario not in SCENARIOS:
        raise ValueError('Unknown replay scenario')
    # Multiples of eight keep the known mixing and correction exactly integral.
    target = [[8 * (abs(((t * (1 + ch % 3) + ch * 7) % 128) - 64) - 32)
               for ch in range(CHANNELS)] for t in range(SAMPLES)]
    raw = []
    for row in target:
        measured = []
        for ch in range(0, CHANNELS, 2):
            measured.extend((row[ch] + row[ch + 1] // 4, 2 * row[ch + 1]))
        if scenario == 'drift':
            measured = [value + (24 if ch < 8 else 0) for ch, value in enumerate(measured)]
        raw.append(measured)
    weights = [[0] * CHANNELS for _ in range(CHANNELS)]
    for ch in range(0, CHANNELS, 2):
        weights[ch][ch] = SCALE
        weights[ch + 1][ch + 1] = SCALE if scenario == 'bypass' else SCALE // 2
        weights[ch + 1][ch] = 0 if scenario == 'bypass' else -SCALE // 8
    return raw, weights, target


def multiply(raw, weights):
    if not raw or len(raw) % CHANNELS or len(weights) != CHANNELS:
        raise ValueError('Expected whole 32x32 frames')
    for row in raw + weights:
        if len(row) != CHANNELS or any(type(v) is not int or abs(v) > 1000 for v in row):
            raise ValueError('Inputs must be 32-wide integers in [-1000,1000]')
    # Independent scalar integer reference: no NumPy, rounding or saturation.
    return [[sum(row[k] * weights[k][ch] for k in range(CHANNELS))
             for ch in range(CHANNELS)] for row in raw]


def input_digest(raw, weights):
    hashed = hashlib.sha256()
    for start in range(0, len(raw), CHANNELS):
        for matrix in (raw[start:start + CHANNELS], weights):
            hashed.update(struct.pack('<1024i', *(v for row in matrix for v in row)))
    return hashed.hexdigest()


def errors(values, target):
    differences = [value - wanted for row, desired in zip(values, target)
                   for value, wanted in zip(row, desired)]
    return {'rmse_counts': math.sqrt(sum(x * x for x in differences) / len(differences)),
            'max_abs_counts': max(abs(x) for x in differences)}


def replay(scenario):
    raw, weights, target = matrices(scenario)
    output_q8 = multiply(raw, weights)
    corrected = [[value / SCALE for value in row] for row in output_q8]
    before, after = errors(raw, target), errors(corrected, target)
    return {'schema': SCHEMA, 'scenario': scenario, 'data_source': 'synthetic_deterministic',
            'samples': SAMPLES, 'channels': CHANNELS, 'frames': SAMPLES // CHANNELS,
            'coefficient_scale': SCALE, 'coefficient_source': 'hand_specified_inverse',
            'raw': raw, 'weights_q8': weights, 'target': target, 'output_q8': output_q8,
            'corrected': corrected, 'input_sha256': input_digest(raw, weights),
            'output_sha256': digest(output_q8), 'before': before, 'after': after,
            'quality_target_met': after['max_abs_counts'] <= 1,
            'quality_limit_counts': 1, 'board_verified': False, 'cpu_speedup': None}


def board_values(scenario):
    """Pack the same reference for the existing guarded driver; no board I/O."""
    import numpy as np
    result = replay(scenario)
    weights = np.array(result['weights_q8'], dtype=np.int32)
    return [(np.array(result['raw'][i:i + 32], dtype=np.int32), weights.copy(),
             np.array(result['output_q8'][i:i + 32], dtype=np.int64))
            for i in range(0, SAMPLES, 32)]
