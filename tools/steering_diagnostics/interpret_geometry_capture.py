"""Describe stored geometry-v2 measurements without running any controller.

Reads the completed capture only. No vehicle access, simulation, or file writes.
"""
import bisect
import collections
import gzip
import json
import statistics
import sys
from pathlib import Path


def load(directory):
    streams = collections.defaultdict(list)
    counts = collections.Counter()
    frames = collections.Counter()
    ages = collections.defaultdict(list)
    base = None
    with gzip.open(directory / 'telemetry.jsonl.gz', 'rt') as source:
        for line in source:
            e = json.loads(line)
            k, d = e['kind'], e['data']
            counts[k] += 1
            if k == 'collector_started':
                base = e['receive_ns'] / 1e9
            if k == 'road_image':
                streams[k].append((d['receive_end_ns'] / 1e9, d))
            if 'source_ns' not in e:
                continue
            t = e['source_ns'] / 1e9
            ages[k].append((e['receive_ns'] - e['source_ns']) / 1e6)
            if k in ('can', 'sendcan'):
                for addr, src, hx in d:
                    frames['%s:%x:%d' % (k, addr, src)] += 1
                    n = int.from_bytes(bytes.fromhex(hx), 'little')
                    if addr == 0x340:
                        streams['%s_lkas_%d' % (k, src)].append((t, {
                            'torque': ((n >> 16) & 2047) - 1024,
                            'request': (n >> 27) & 1, 'fault_request': (n >> 28) & 1,
                            'counter': (n >> 36) & 15}))
                    elif k == 'can' and addr == 0x251 and src == 0:
                        streams['mdps'].append((t, {
                            'driver': (n & 2047) - 1024, 'unavailable': (n >> 12) & 1,
                            'active': (n >> 13) & 1, 'fault': (n >> 14) & 1,
                            'fail': (n >> 15) & 1, 'error': (n >> 37) & 1,
                            'output': round(((n >> 52) & 4095) * .1 - 204.8, 2)}))
                    elif k == 'can' and addr == 0x2b0 and src == 0:
                        a = n & 65535
                        streams['sas'].append((t, {
                            'angle': (a - 65536 if a >= 32768 else a) * .1,
                            'rate_unsigned': ((n >> 16) & 255) * 4}))
            else:
                if isinstance(d, dict):
                    d['_valid'] = e['valid']
                streams[k].append((t, d))
    for rows in streams.values():
        rows.sort(key=lambda p: p[0])
    return base, streams, counts, frames, ages


class Capture:
    def __init__(self, directory):
        self.base, self.streams, self.counts, self.frames, self.ages = load(directory)
        self.times = {k: [t for t, _ in rows] for k, rows in self.streams.items()}

    def near(self, name, elapsed):
        ts = self.times[name]
        t = self.base + elapsed
        i = bisect.bisect_left(ts, t)
        j = min(range(max(0, i - 1), min(len(ts), i + 1)), key=lambda j: abs(ts[j] - t))
        st, data = self.streams[name][j]
        return data, round((st - t) * 1000, 3)

    def intervals(self, name, predicate, minimum=.3, gap=.2):
        groups = []
        first = last = None
        for t, d in self.streams[name]:
            if not predicate(d):
                continue
            if first is None:
                first = t
            elif t - last > gap:
                if last - first >= minimum:
                    groups.append([round(first - self.base, 3), round(last - self.base, 3)])
                first = t
            last = t
        if first is not None and last - first >= minimum:
            groups.append([round(first - self.base, 3), round(last - self.base, 3)])
        return groups

    def sample(self, elapsed):
        names = ['carState', 'carControl', 'controlsState', 'mdps', 'sas', 'can_lkas_128',
                 'modelV2', 'lateralPlan', 'liveLocationKalman', 'liveParameters', 'road_image']
        chosen = {name: self.near(name, elapsed) for name in names if name in self.streams}
        cs, cc, ctl = [chosen[name][0] for name in names[:3]]
        md, sas, tx = [chosen[name][0] for name in names[3:6]]
        model, plan, loc, live = [chosen[name][0] for name in names[6:10]]
        lat = ctl['lateral']
        result = {
            't': round(elapsed, 3), 'speed_kph': round(cs['vEgo'] * 3.6, 2),
            'wheel_kph': round(cs['vEgoRaw'] * 3.6, 2), 'angle': round(sas['angle'], 1),
            'op': ctl['active'], 'command': round(cc['actuators']['steer'] * 384, 1),
            'host': round(cc['actuatorsOutput']['steer'] * 384, 1), 'tx': tx,
            'mdps': md, 'pressed': cs['steeringPressed'],
            'generic_fault': cs['steerFaultTemporary'] or cs['steerFaultPermanent'],
            'pif': {k: round(lat.get(k, 0), 4) for k in ['p', 'i', 'f', 'desiredLateralAccel', 'actualLateralAccel']},
            'curvature0': plan['curvatures'][0], 'laneless': plan['lanelessMode'],
            'lane_probs': [round(v, 3) for v in model['laneLineProbs']],
            'edge_stds': [round(v, 3) for v in model['roadEdgeStds']],
            'model_frame': model['frameId'], 'model_eof_ns': model['timestampEof'],
            'angular_velocity': loc['angularVelocityCalibrated'],
            'live_parameters_valid': live['valid'],
            'nearest_offset_ms': {k: v[1] for k, v in chosen.items()},
        }
        if 'road_image' in chosen:
            result['image'] = chosen['road_image'][0]['image_file']
        return result

    def summary(self):
        out = {'counts': self.counts, 'frames': self.frames,
               'duration_s': max(rows[-1][0] for rows in self.streams.values()) - self.base,
               'max_speed_kph': max(d['vEgo'] * 3.6 for t, d in self.streams['carState']),
               'op_active': self.intervals('controlsState', lambda d: d['active']),
               'moving': self.intervals('carState', lambda d: d['vEgo'] * 3.6 > 1, gap=1),
               'maximum_request': self.intervals('can_lkas_128', lambda d: d['request'] and abs(d['torque']) >= 380),
               'large_angle_moving': self.intervals('carState', lambda d: d['vEgo'] * 3.6 > 1 and abs(d['steeringAngleDeg']) > 35, gap=.5),
               'manual_driver_over150': self.intervals('mdps', lambda d: abs(d['driver']) > 150),
               'mdps_fault': self.intervals('mdps', lambda d: d['fault'], minimum=0, gap=.03),
               'mdps_fail': self.intervals('mdps', lambda d: d['fail'], minimum=0, gap=.03),
               'delivery_ms': {k: {'median': round(statistics.median(a), 2), 'p99': round(sorted(a)[int(len(a)*.99)], 2)} for k, a in self.ages.items()}}
        return out


if __name__ == '__main__':
    capture = Capture(Path(sys.argv[1]))
    if len(sys.argv) == 2:
        print(json.dumps(capture.summary(), indent=2))
    else:
        start, end, step = map(float, sys.argv[2:5])
        while start <= end:
            print(json.dumps(capture.sample(start), separators=(',', ':')))
            start += step
