"""Receive existing C2 messages only; execute via SSH stdin, never publish CAN."""
import collections
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

import cereal.messaging as messaging
from cereal import car
from common.params import Params

SECONDS = min(900, max(1, int(sys.argv[1])))
signal.alarm(SECONDS + 30)
try:
    os.nice(10)
except OSError:
    pass

PARAM_KEYS = """OpkrMaxAngleLimit OpkrSteerMethod OpkrMaxSteeringAngle
AvoidLKASFaultEnabled AvoidLKASFaultMaxAngle AvoidLKASFaultMaxFrame
AvoidLKASFaultBeyond NoSmartMDPS OpkrLaneChangeSpeed IsMetric SteerThreshold
OpkrTurnSteeringDisable SteerMaxBaseAdj SteerMaxAdj SteerDeltaUpBaseAdj
SteerDeltaUpAdj SteerDeltaDownBaseAdj SteerDeltaDownAdj OpkrVariableSteerMax
OpkrVariableSteerDelta LateralControlMethod TorqueKp TorqueKi TorqueKf
TorqueFriction TorqueMaxLatAccel TorqueAngDeadZone TorqueUseAngle
OpkrLiveTunePanelEnable OpkrEnableLogger SteerActuatorDelayAdj
TireStiffnessFactorAdj LiveSteerRatioPercent OpkrSteerAngleCorrection""".split()
FIELDS = {
    'carState': 'vEgo vEgoRaw steeringAngleDeg steeringRateDeg steeringTorque steeringTorqueEps steeringPressed steeringRateLimited steerFaultTemporary steerFaultPermanent yawRate brakePressed brakeLights gasPressed gearShifter standstill leftBlinker rightBlinker cruiseButtons canValid'.split(),
    'controlsState': 'state enabled active curvature alertText1 alertText2 lateralPlanMonoTime steer'.split(),
    'liveParameters': 'valid sensorValid posenetValid steerRatio stiffnessFactor angleOffsetDeg angleOffsetAverageDeg roll'.split(),
    'lateralPlan': 'modelMonoTime mpcSolutionValid psis curvatures curvatureRates dPathPoints laneChangeState laneChangeDirection useLaneLines lanelessMode lProb rProb modelSpeed'.split(),
}
SERVICES = ['can', 'sendcan', 'carState', 'carControl', 'controlsState',
            'pandaStates', 'liveParameters', 'lateralPlan', 'modelV2']
# Only steering, wheel-speed, yaw/brake, and physical cruise status frames.
CAN_IDS = {0x340, 0x251, 0x2B0, 0x386, 0x220, 0x595, 0x4F1}
counts = collections.Counter()
can_counts = collections.Counter()
last = {}
max_delivery_age_ms = 0.0


def emit(kind, data, source_ns=None, valid=None):
    out = {'kind': kind, 'receive_ns': time.monotonic_ns(), 'data': data}
    if source_ns is not None:
        out['source_ns'] = int(source_ns)
        out['valid'] = bool(valid)
    print(json.dumps(out, separators=(',', ':'), default=str), flush=False)


def select_fields(value, names):
    available = value.schema.fields
    out = {}
    for name in names:
        if name not in available:
            continue
        item = getattr(value, name)
        if isinstance(item, (bool, int, float, str)):
            out[name] = item
        else:
            try:
                out[name] = list(item)
            except TypeError:
                out[name] = str(item)
    return out


p = Params()
params = {key: p.get(key, encoding='utf8') for key in PARAM_KEYS}
cp_raw = p.get('CarParams')
cp_info = {}
if cp_raw:
    cp = car.CarParams.from_bytes(cp_raw)
    cp_info = select_fields(cp, ['carFingerprint', 'mdpsBus', 'sccBus', 'sasBus',
                                'steerActuatorDelay', 'steerRatio', 'wheelbase'])
    cp_info['smoothSteer'] = cp.smoothSteer.to_dict()
    cp_info['lateralTuning'] = cp.lateralTuning.to_dict()
    cp_info['eps_firmware_hex'] = [bytes(f.fwVersion).hex() for f in cp.carFw if str(f.ecu) == 'eps']
signature = p.get('PandaSignatures') or b''
emit('metadata', {'wall_time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip(),
                  'params': params, 'car_params': cp_info, 'seconds_limit': SECONDS,
                  'panda_signature_sha256': hashlib.sha256(signature).hexdigest(),
                  'read_only': True, 'can_ids': sorted(CAN_IDS),
                  'can_frame_columns': ['address', 'src', 'data_hex'],
                  'src_note': 'base bus; +128 transmitted; +192 rejected. Echo is not EPS actuation confirmation.',
                  'eps_output_note': 'MDPS output report has unspecified physical unit in this DBC.',
                  'host_note': 'actuatorsOutput is host-limited command, not actual EPS output; internal attenuation timer is not published.'})

poller = messaging.Poller()
sockets = [messaging.sub_sock(name, poller=poller, conflate=False) for name in SERVICES]
start = time.monotonic()
next_status = start
emit('ready', {'services': SERVICES})
sys.stdout.flush()
while time.monotonic() - start < SECONDS:
    for sock in poller.poll(100):
        # A finite per-socket drain prevents a busy topic starving the others.
        for _ in range(200):
            event = messaging.recv_one_or_none(sock)
            if event is None:
                break
            service = event.which()
            counts[service] += 1
            age_ms = (time.monotonic_ns() - int(event.logMonoTime)) / 1e6
            max_delivery_age_ms = max(max_delivery_age_ms, age_ms)
            value = getattr(event, service)
            if service in ('can', 'sendcan'):
                data = [[int(f.address), int(f.src), bytes(f.dat).hex()]
                        for f in value if int(f.address) in CAN_IDS]
                if not data:
                    continue
                for address, src, hexdata in data:
                    can_counts['%s:%x:%d' % (service, address, src)] += 1
                    bits = int.from_bytes(bytes.fromhex(hexdata), 'little')
                    if service == 'can' and address == 0x251 and src == 0:
                        last['mdps'] = {'source_ns': int(event.logMonoTime),
                                        'active': (bits >> 13) & 1,
                                        'unavailable': (bits >> 12) & 1,
                                        'fault': (bits >> 14) & 1,
                                        'fail': (bits >> 15) & 1,
                                        'output_reported': ((bits >> 52) & 4095) * 0.1 - 204.8}
            elif service in FIELDS:
                data = select_fields(value, FIELDS[service])
                if service == 'carState':
                    data['cruiseState'] = value.cruiseState.to_dict()
                    data['events'] = [str(e.name) for e in value.events]
                    last['car'] = {'speed_kph': float(value.vEgo) * 3.6,
                                   'angle_deg': float(value.steeringAngleDeg),
                                   'driver_torque': float(value.steeringTorque),
                                   'pressed': bool(value.steeringPressed)}
                elif service == 'controlsState':
                    lateral = value.lateralControlState
                    tag = lateral.which()
                    data['lateral_type'] = tag
                    data['lateral'] = getattr(lateral, tag).to_dict()
                    last['op_active'] = bool(value.active)
            elif service == 'carControl':
                data = select_fields(value, ['enabled', 'active', 'latActive'])
                data['actuators'] = value.actuators.to_dict()
                data['actuatorsOutput'] = value.actuatorsOutput.to_dict()
                last['command'] = float(value.actuators.steer)
                last['host_final'] = float(value.actuatorsOutput.steer)
            elif service == 'pandaStates':
                data = [select_fields(x, ['controlsAllowed', 'blockedCnt', 'canSendErrs',
                                         'canRxErrs', 'safetyModel', 'safetyParam',
                                         'heartbeatLost', 'faultStatus', 'faults', 'uptime']) for x in value]
            elif service == 'modelV2':
                data = {'position_x': list(value.position.x), 'position_y': list(value.position.y),
                        'time': list(value.position.t), 'yaw': list(value.orientation.z),
                        'yaw_rate': list(value.orientationRate.z),
                        'lane_probabilities': list(value.laneLineProbs)}
            else:
                continue
            emit(service, data, event.logMonoTime, event.valid)
    now = time.monotonic()
    if now >= next_status:
        emit('status', {'elapsed_s': round(now - start, 1), 'counts': dict(counts),
                        'can_counts': dict(can_counts), 'last': last,
                        'max_delivery_age_ms': round(max_delivery_age_ms, 1)})
        sys.stdout.flush()
        next_status = now + 10
emit('end', {'reason': 'duration_limit', 'elapsed_s': time.monotonic() - start,
             'counts': dict(counts), 'can_counts': dict(can_counts)})
sys.stdout.flush()
