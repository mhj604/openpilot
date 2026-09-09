"""Read saved telemetry; describe the recorded path-to-torque chain.

No vehicle access, controller/MPC execution, parameter changes, or output files.
Derived quantities describe recorded data, not a simulated successful maneuver.
"""
import bisect
import collections
import gzip
import json
import math
import statistics
import sys
from pathlib import Path


def interp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    j = bisect.bisect_right(xs, x)
    f = (x - xs[j - 1]) / (xs[j] - xs[j - 1])
    return ys[j - 1] * (1 - f) + ys[j] * f


def three_point_curvature(xs, ys, x, span=2.0):
    # Signed geometric curvature of three points on the predicted path.
    # This is not measured road curvature or MPC curvature at time zero.
    p = [(q, interp(q, xs, ys)) for q in (x - span, x, x + span)]
    a = math.dist(p[0], p[1])
    b = math.dist(p[1], p[2])
    c = math.dist(p[0], p[2])
    cross = ((p[1][0] - p[0][0]) * (p[2][1] - p[0][1])
             - (p[1][1] - p[0][1]) * (p[2][0] - p[0][0]))
    return 2 * cross / (a * b * c) if a * b * c > 0 else None


def load_window(path, start=198, end=222):
    base = None
    streams = collections.defaultdict(list)
    with gzip.open(path, 'rt') as f:
        for line in f:
            e = json.loads(line)
            if e['kind'] == 'ready':
                base = e['receive_ns']
            if base is None or 'source_ns' not in e:
                continue
            if (e['receive_ns'] - base) / 1e9 > end + 3:
                break
            if start <= (e['source_ns'] - base) / 1e9 <= end:
                if e['kind'] not in ('can', 'sendcan'):
                    streams[e['kind']].append(e)
    for rows in streams.values():
        rows.sort(key=lambda e: e['source_ns'])
    return base, dict(streams)


def summary(values):
    return {'min': min(values), 'median': statistics.median(values), 'max': max(values)} if values else None


def main(directory):
    base, streams = load_window(directory / 'telemetry.jsonl.gz')
    times = {k: [e['source_ns'] for e in rows] for k, rows in streams.items()}
    plans = {e['source_ns']: e for e in streams['lateralPlan']}
    models = {e['source_ns']: e for e in streams['modelV2']}
    meta = json.loads((directory / 'metadata.json').read_text())['data']
    delay = meta['car_params']['steerActuatorDelay']
    max_ay = float(meta['params']['TorqueMaxLatAccel']) * .1

    def nearest(k, ns):
        ts = times[k]
        i = bisect.bisect_left(ts, ns)
        j = min(range(max(0, i - 1), min(len(ts), i + 1)), key=lambda j: abs(ts[j] - ns))
        return streams[k][j]

    def elapsed(e):
        return (e['source_ns'] - base) / 1e9

    rows = []
    for e in streams['controlsState']:
        t = elapsed(e)
        if not 204 <= t <= 219:
            continue
        c = e['data']
        p = plans.get(c['lateralPlanMonoTime'])
        if p is None:
            continue
        m = models.get(p['data']['modelMonoTime'])
        if m is None:
            continue
        pe, me = p, m
        p, m = p['data'], m['data']
        ce = nearest('carState', e['source_ns'])
        ae = nearest('carControl', e['source_ns'])
        le = nearest('liveParameters', e['source_ns'])
        cs, ac, lp = ce['data'], ae['data'], le['data']
        v = cs['vEgo']
        lat = c['lateral']
        ts = m['time'][:17]
        k0 = p['curvatures'][0]
        k_unclipped = 2 * interp(delay, ts, p['psis']) / (max(v, .1) * delay) - k0
        # Historical DesiredCurvatureLimit was not captured. The manager default
        # is .10 s; the missing-key fallback in drive_helpers is .05 s.
        # These are labeled formula interpretations, not controller executions.
        k05 = max(k0 - 5 / v**2 * .05, min(k0 + 5 / v**2 * .05, k_unclipped))
        k10 = max(k0 - 5 / v**2 * .10, min(k0 + 5 / v**2 * .10, k_unclipped))
        pid_sum = sum(lat.get(x, 0) for x in ('p', 'i', 'd', 'f'))
        expected_kp = interp(v, [1, 1.5, 2, 3, 5, 7.5, 10, 15, 30],
                             [250, 120, 65, 30, 11.5, 5.5, 3.5, 2, .8]) / .8
        xs, ys = m['position_x'], m['position_y']
        # Ignore initial backward model points for this spatial comparison.
        ix = [i for i, x in enumerate(xs) if x >= 0]
        xy_m = [(xs[i], ys[i]) for i in ix]
        xm, ym = zip(*xy_m)
        distances = [v * h for h in ts]
        path_differences = [p['dPathPoints'][i] - interp(distances[i],
                            [math.hypot(x, y) for x, y in xy_m], ym)
                            for i in range(5, 17)] if p['lanelessMode'] else []
        # Infer the planner's sampling speed from multiple monotonic path segments.
        # This handles carState changing between planner and controls messages.
        # model.position.z was not recorded, so tiny norm residuals remain unknown.
        inferred_speeds = []
        if p['lanelessMode']:
            rs = [math.hypot(x, y) for x, y in xy_m]
            for i in range(7, 17):
                yq = p['dPathPoints'][i]
                candidates = []
                for j in range(1, len(ym)):
                    if ym[j] != ym[j-1] and min(ym[j-1], ym[j]) <= yq <= max(ym[j-1], ym[j]):
                        f = (yq-ym[j-1])/(ym[j]-ym[j-1])
                        rq = rs[j-1]*(1-f)+rs[j]*f
                        candidates.append(rq/ts[i])
                if candidates:
                    inferred_speeds.append(min(candidates,key=lambda s:abs(s-v)))
        pv = statistics.median(inferred_speeds) if inferred_speeds else None
        aligned_differences = [p['dPathPoints'][i]-interp(pv*ts[i],rs,ym)
                               for i in range(7,17)] if pv else []
        row = {
            't': t, 'v_kph': v * 3.6, 'v_raw_kph': cs['vEgoRaw'] * 3.6,
            'angle_deg': cs['steeringAngleDeg'], 'driver_pressed': cs['steeringPressed'],
            'driver_torque': cs['steeringTorque'], 'op_active': c['active'],
            'lateral_active': lat['active'], 'use_lines': p['useLaneLines'],
            'laneless': p['lanelessMode'], 'lane_change': p['laneChangeState'],
            'lane_prob': [p['lProb'], p['rProb']], 'model_y0': ys[0],
            'model_y_5_10_15m': [interp(x, xm, ym) for x in (5, 10, 15)],
            'model_geometric_curv_5_10_15m': [three_point_curvature(xm, ym, x) for x in (5, 10, 15)],
            'plan_y_at_horizons': [interp(h, ts, p['dPathPoints']) for h in (.36, .7, 1, 1.5, 2.5)],
            'model_yaw_spatial_at_horizons': [interp(v*h, [math.hypot(x,y) for x,y in zip(xs[3:],ys[3:])], m['yaw'][3:]) for h in (.36,.7,1,1.5,2.5)],
            'plan_yaw_at_horizons': [interp(h, ts, p['psis']) for h in (.36,.7,1,1.5,2.5)],
            'plan_curv_at_horizons': [interp(h, ts, p['curvatures']) for h in (0,.36,.7,1,1.5,2.5)],
            'k_before_clip': k_unclipped, 'k_assuming_limit05': k05, 'k_assuming_limit10': k10,
            'ay_before_clip': k_unclipped*v*v,
            'ay_assuming_limit05': k05*v*v, 'ay_assuming_limit10': k10*v*v, 'roll': lp['roll'],
            'error': lat['error'], 'desired_ay_delayed': lat['desiredLateralAccel'],
            'actual_ay_model': lat['actualLateralAccel'],
            'p': lat['p'], 'i': lat['i'], 'f': lat['f'],
            'p_from_source_formula': lat['error'] * expected_kp,
            'unclipped_normalized': pid_sum/max_ay,
            'raw_request': ac['actuators']['steer'] * 384,
            'host_request': ac['actuatorsOutput']['steer'] * 384,
            'torque_log_output': lat['output'],
            'actuator_angle_placeholder': ac['actuators']['steeringAngleDeg'],
            'path_y_difference_max_5_16': max(map(abs, path_differences)) if path_differences else None,
            'plan_speed_inferred_kph': pv*3.6 if pv else None,
            'plan_speed_inferred_spread_kph': (max(inferred_speeds)-min(inferred_speeds))*3.6 if inferred_speeds else None,
            'path_y_difference_speed_aligned_m': max(map(abs,aligned_differences)) if aligned_differences else None,
            'speed_gap_kph': (v-cs['vEgoRaw'])*3.6,
            'speed_squared_ratio': (v/cs['vEgoRaw'])**2 if cs['vEgoRaw'] else None,
            'plan_age_ms': (e['source_ns']-pe['source_ns'])/1e6,
            'model_age_ms': (e['source_ns']-me['source_ns'])/1e6,
            'car_nearest_ms': abs(e['source_ns']-ce['source_ns'])/1e6,
            'actuator_nearest_ms': abs(e['source_ns']-ae['source_ns'])/1e6,
        }
        rows.append(row)

    print('COUNTS', {k:len(v) for k,v in streams.items()}, 'linked_controls',len(rows))
    prev = None
    for e in streams['lateralPlan']:
        d = e['data']
        mode = (d['useLaneLines'],d['lanelessMode'],d['laneChangeState'])
        if mode != prev:
            print('MODE',elapsed(e),mode)
            prev = mode
    for t in (204,206,208,209,209.5,210,210.5,211,211.5,212,212.3,212.5,213,213.3,213.5,214,215,216,218):
        r=min(rows,key=lambda r:abs(r['t']-t))
        if '--full' in sys.argv:
            print('SAMPLE',json.dumps(r,ensure_ascii=False))
        else:
            keys = ('t','v_kph','v_raw_kph','angle_deg','laneless','lane_prob','model_y_5_10_15m',
                    'plan_curv_at_horizons','k_before_clip','k_assuming_limit10',
                    'desired_ay_delayed','actual_ay_model','p','i','f','unclipped_normalized',
                    'raw_request','host_request','plan_speed_inferred_kph','path_y_difference_speed_aligned_m')
            print('SAMPLE',json.dumps({k:r[k] for k in keys},ensure_ascii=False))
    core=[r for r in rows if 211.5<=r['t']<=212.4]
    print('CORE',json.dumps({k:summary([r[k] for r in core]) for k in
        ('plan_age_ms','model_age_ms','car_nearest_ms','actuator_nearest_ms','unclipped_normalized',
         'raw_request','host_request','desired_ay_delayed','actual_ay_model','error','p','f',
         'path_y_difference_max_5_16','path_y_difference_speed_aligned_m',
         'speed_gap_kph','speed_squared_ratio','plan_speed_inferred_spread_kph')},ensure_ascii=False))
    print('CORE_P_FORMULA_DIFFERENCE',summary([r['p']-r['p_from_source_formula'] for r in core]))
    print('CORE_ERROR_IDENTITY',summary([r['desired_ay_delayed']-r['actual_ay_model']-r['error'] for r in core]))
    print('CORE_RAW_OUTPUT_DIFFERENCE',summary([r['raw_request']/384-r['torque_log_output'] for r in core]))
    # Per-plan changes in a fixed lookahead are descriptive only: the ego frame moves.
    pwin=[e for e in streams['lateralPlan'] if 208<=elapsed(e)<=213.3]
    for horizon in (0,.36,.7,1):
        diffs=[]
        for a,b in zip(pwin,pwin[1:]):
            ka=interp(horizon,[10*(i/32)**2 for i in range(17)],a['data']['curvatures'])
            kb=interp(horizon,[10*(i/32)**2 for i in range(17)],b['data']['curvatures'])
            diffs.append((abs(kb-ka),elapsed(b),ka,kb))
        print('LARGEST_PLAN_STEP',horizon,sorted(diffs,reverse=True)[:3])


if __name__=='__main__':
    main(Path(sys.argv[1]))
