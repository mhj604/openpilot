"""Offline interpretation of an existing, receive-only JSONL recording."""
import bisect
import collections
import gzip
import json
import pickle
import statistics
import sys
from pathlib import Path


def read_capture(directory):
    streams = collections.defaultdict(list)
    counts = collections.Counter()
    frames = collections.Counter()
    ages = collections.defaultdict(list)
    broken = []
    base = None
    with gzip.open(directory / 'telemetry.jsonl.gz', 'rt') as f:
        for lineno, line in enumerate(f, 1):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                broken.append(lineno)
                continue
            k, d = e['kind'], e['data']
            counts[k] += 1
            if k == 'ready':
                base = e['receive_ns'] / 1e9
            if 'source_ns' not in e:
                continue
            t = e['source_ns'] / 1e9
            ages[k].append((e['receive_ns'] - e['source_ns']) / 1e6)
            if k in ('can', 'sendcan'):
                for addr, src, hx in d:
                    frames[f'{k}:{addr:x}:{src}'] += 1
                    n = int.from_bytes(bytes.fromhex(hx), 'little')
                    if addr == 0x340:
                        streams[f'{k}_lkas_{src}'].append((t, {
                            'torque': ((n >> 16) & 2047) - 1024,
                            'request': (n >> 27) & 1, 'fault_request': (n >> 28) & 1,
                            'counter': (n >> 36) & 15, 'hex': hx}))
                    elif k == 'can' and addr == 0x251 and src == 0:
                        streams['mdps'].append((t, {
                            'driver': (n & 2047) - 1024, 'unavailable': (n >> 12) & 1,
                            'active': (n >> 13) & 1, 'fault': (n >> 14) & 1,
                            'fail': (n >> 15) & 1, 'error': (n >> 37) & 1,
                            'counter': (n >> 16) & 255,
                            'steering_torque': ((n >> 40) & 4095) * .01 - 20.48,
                            'output': round(((n >> 52) & 4095) * .1 - 204.8, 2)}))
                    elif k == 'can' and addr == 0x2b0 and src == 0:
                        a = n & 65535
                        streams['sas'].append((t, {'angle': (a - 65536 if a >= 32768 else a) * .1,
                                                   'rate_unsigned': ((n >> 16) & 255) * 4}))
            elif k != 'modelV2':
                if k == 'lateralPlan':
                    d = {key: d[key] for key in ('curvatures', 'mpcSolutionValid', 'laneChangeState', 'modelSpeed')}
                streams[k].append((t, d))
    for rows in streams.values():
        rows.sort(key=lambda x: x[0])
    result = {'streams': dict(streams), 'base': base, 'counts': dict(counts),
              'frames': dict(frames), 'broken_lines': broken}
    result['delivery_ms'] = {k: {'median': statistics.median(a), 'p99': sorted(a)[int(len(a)*.99)], 'max': max(a)}
                             for k, a in ages.items()}
    with (directory / 'decoded-recording.pickle').open('wb') as f:
        pickle.dump(result, f, protocol=4)
    return result


class Recording:
    def __init__(self, result):
        self.result = result
        self.base = result['base']
        self.streams = result['streams']
        self.times = {k: [t for t, d in rows] for k, rows in self.streams.items()}

    def nearest(self, k, t):
        ts = self.times[k]
        i = bisect.bisect_left(ts, t)
        i = min(range(max(0, i-1), min(len(ts), i+1)), key=lambda x: abs(ts[x]-t))
        return self.streams[k][i]

    def sample(self, elapsed):
        t = elapsed + self.base
        chosen = {k: self.nearest(k, t) for k in ('carState', 'carControl', 'controlsState', 'mdps', 'sas', 'can_lkas_128', 'sendcan_lkas_0')}
        cs = chosen['carState'][1]
        cc = chosen['carControl'][1]
        ctl = chosen['controlsState'][1]
        md = chosen['mdps'][1]
        tx = chosen['can_lkas_128'][1]
        return {'t': round(elapsed, 3), 'v': round(cs['vEgo']*3.6, 2),
                'angle': round(chosen['sas'][1]['angle'], 1), 'rate': cs['steeringRateDeg'],
                'op': ctl['active'], 'lat': cc.get('active', cc.get('latActive')),
                'cmd': round(cc['actuators']['steer']*384, 1),
                'host': round(cc['actuatorsOutput']['steer']*384),
                'send': chosen['sendcan_lkas_0'][1]['torque'], 'tx': tx['torque'], 'req': tx['request'],
                'driver': md['driver'], 'pressed': cs['steeringPressed'],
                'eps': md['output'], 'mdactive': md['active'], 'fault': md['fault'],
                'fail': md['fail'], 'unavail': md['unavailable'],
                'generic_fault': cs['steerFaultTemporary'] or cs['steerFaultPermanent'],
                'limited': cs['steeringRateLimited'], 'p': round(ctl['lateral'].get('p', 0), 3),
                'i': round(ctl['lateral'].get('i', 0), 3),
                'desired_ay': round(ctl['lateral'].get('desiredLateralAccel', 0), 3),
                'actual_ay': round(ctl['lateral'].get('actualLateralAccel', 0), 3),
                'max_nearest_dt_ms': round(max(abs(st-t)*1000 for st, _ in chosen.values()), 1)}

    def intervals(self, key, predicate, min_duration=0, merge_gap=0):
        groups = []
        start = last = None
        for t, d in self.streams[key]:
            if predicate(d):
                if start is None:
                    start = t
                elif t-last > merge_gap + .1:
                    groups.append((start-self.base, last-self.base))
                    start = t
                last = t
        if start is not None:
            groups.append((start-self.base, last-self.base))
        return [(s, e) for s, e in groups if e-s >= min_duration]


def summary(r):
    out = {k: r.result[k] for k in ('counts', 'frames', 'broken_lines', 'delivery_ms')}
    out['stream_timing'] = {k: {'start': rows[0][0]-r.base, 'end': rows[-1][0]-r.base,
                               'max_gap_ms': max(b[0]-a[0] for a, b in zip(rows, rows[1:]))*1000}
                             for k, rows in r.streams.items() if len(rows)>1}
    for flag in ('fault', 'fail', 'unavailable', 'error'):
        out['mdps_'+flag] = r.intervals('mdps', lambda d: d[flag])
    out['curves'] = r.intervals('carState', lambda d: abs(d['steeringAngleDeg'])>40 and d['vEgo']*3.6>1,
                                 min_duration=.5, merge_gap=1)
    out['panda_values'] = {k: sorted(set(json.dumps(d[0].get(k)) for t, d in r.streams['pandaStates'] if d))
                           for k in ('controlsAllowed', 'blockedCnt', 'canSendErrs', 'canRxErrs', 'faults', 'heartbeatLost')}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    for s, e in out['curves']:
        rows = [(t, d) for t, d in r.streams['carState'] if s <= t-r.base <= e]
        t, d = max(rows, key=lambda x: abs(x[1]['steeringAngleDeg']))
        print('CURVE', round(s,2), round(e,2), json.dumps(r.sample(t-r.base)))


if __name__ == '__main__':
    directory = Path(sys.argv[1])
    cached = directory / 'decoded-recording.pickle'
    if cached.exists():
        with cached.open('rb') as f:
            result = pickle.load(f)
    else:
        result = read_capture(directory)
    r = Recording(result)
    if len(sys.argv) == 2:
        summary(r)
    else:
        start, end, step = map(float, sys.argv[2:5])
        while start <= end:
            print(json.dumps(r.sample(start), ensure_ascii=False))
            start += step
