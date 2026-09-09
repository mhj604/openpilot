"""Bounded receive-only collection; no Params writes, CAN sends, or daemon starts.

Run over SSH stdin from /data/openpilot. Images come only from an already
running road-camera publisher. The old Python VisionIPC binding omits frame
metadata: JPEG receive times are NOT exact exposure timestamps or frame IDs.
"""
import base64
import collections
import hashlib
import io
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

import cereal.messaging as messaging
from cereal import car
from cereal.services import service_list
from common.params import Params

SECONDS = min(900, max(1, int(sys.argv[1])))
signal.alarm(SECONDS + 30)
try:
    os.nice(10)
except OSError:
    pass

PARAM_KEYS = """OpkrMaxAngleLimit OpkrSteerMethod OpkrMaxSteeringAngle
AvoidLKASFaultEnabled AvoidLKASFaultMaxAngle AvoidLKASFaultMaxFrame
AvoidLKASFaultBeyond NoSmartMDPS IsMetric SteerThreshold OpkrTurnSteeringDisable
SteerMaxBaseAdj SteerMaxAdj SteerDeltaUpBaseAdj SteerDeltaUpAdj
SteerDeltaDownBaseAdj SteerDeltaDownAdj OpkrVariableSteerMax OpkrVariableSteerDelta
LateralControlMethod TorqueKp TorqueKi TorqueKf TorqueFriction TorqueMaxLatAccel
TorqueAngDeadZone TorqueUseAngle OpkrLiveTunePanelEnable OpkrEnableLogger
SteerActuatorDelayAdj TireStiffnessFactorAdj LiveSteerRatioPercent
OpkrSteerAngleCorrection DesiredCurvatureLimit LanelessMode EndToEndToggle""".split()
FIELDS = {
    'carState': 'vEgo vEgoRaw vEgoOP aEgo wheelSpeeds steeringAngleDeg steeringRateDeg steeringTorque steeringTorqueEps steeringPressed steeringRateLimited steerFaultTemporary steerFaultPermanent yawRate brakePressed brakeLights gasPressed gearShifter standstill leftBlinker rightBlinker cruiseButtons canValid cruiseState events'.split(),
    'controlsState': 'state enabled active curvature alertText1 alertText2 lateralPlanMonoTime steer'.split(),
    'liveParameters': 'valid sensorValid posenetValid steerRatio stiffnessFactor angleOffsetDeg angleOffsetAverageDeg roll'.split(),
    'lateralPlan': 'modelMonoTime mpcSolutionValid psis curvatures curvatureRates dPathPoints laneChangeState laneChangeDirection useLaneLines lanelessMode lProb rProb dProb laneWidth modelSpeed totalCameraOffset solverExecutionTime'.split(),
    'liveLocationKalman': 'status inputsOK posenetOK sensorsOK deviceStable excessiveResets timeSinceReset velocityCalibrated accelerationCalibrated angularVelocityCalibrated calibratedOrientationNED'.split(),
    'liveCalibration': 'calStatus calPerc validBlocks rpyCalib rpyCalibSpread extrinsicMatrix'.split(),
    'cameraOdometry': 'frameId timestampEof trans rot transStd rotStd'.split(),
    'roadCameraState': 'frameId timestampSof timestampEof processingTime frameLength transform'.split(),
    'deviceState': 'thermalStatus freeSpacePercent cpuUsagePercent memoryUsagePercent cpuTempC gpuTempC batteryTempC'.split(),
}
REQUESTED = ['can', 'sendcan', 'carState', 'carControl', 'controlsState',
             'pandaStates', 'liveParameters', 'lateralPlan', 'modelV2',
             'liveLocationKalman', 'liveCalibration', 'cameraOdometry',
             'roadCameraState', 'thumbnail', 'deviceState']
SERVICES = [name for name in REQUESTED if name in service_list]
CAN_IDS = {0x340, 0x251, 0x2B0, 0x386, 0x220, 0x595, 0x4F1}
MODEL_FIELDS = 'frameId frameIdExtra frameAge frameDropPerc timestampEof modelExecutionTime position orientation velocity orientationRate acceleration laneLines laneLineProbs laneLineStds roadEdges roadEdgeStds'.split()
counts = collections.Counter()
last_source = {}
last_valid = {}
last = {}
image_queue = queue.Queue(maxsize=2)
stop_images = threading.Event()
image_state = {'state': 'starting', 'frames_captured': 0, 'frames_emitted': 0,
               'frames_dropped': 0, 'last_receive_ns': None}


def plain(value):
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if hasattr(value, 'to_dict'):
        return value.to_dict()
    try:
        return [plain(item) for item in value]
    except TypeError:
        return str(value)


def select(value, names):
    available = value.schema.fields
    return {name: plain(getattr(value, name)) for name in names if name in available}


def emit(kind, data, source_ns=None, valid=None):
    item = {'kind': kind, 'receive_ns': time.monotonic_ns(), 'data': data}
    if source_ns is not None:
        item.update(source_ns=int(source_ns), valid=bool(valid))
    print(json.dumps(item, separators=(',', ':'), default=str))


def road_images():
    try:
        from PIL import Image
        from cereal.visionipc.visionipc_pyx import VisionIpcClient, VisionStreamType
        client = VisionIpcClient('camerad', VisionStreamType.VISION_STREAM_RGB_ROAD, True)
        slow_encodes = 0
        while not stop_images.is_set():
            if not client.is_connected():
                image_state['state'] = 'waiting_for_existing_camera'
                if not client.connect(False):
                    stop_images.wait(0.5)
                    continue
            before = time.monotonic_ns()
            buf = client.recv(100)
            after = time.monotonic_ns()
            if buf is None:
                image_state['state'] = 'waiting_for_camera_frame'
                stop_images.wait(0.1)
                continue
            width, height, stride = int(client.width), int(client.height), int(client.stride)
            image = Image.frombytes('RGB', (width, height), buf.tobytes(), 'raw', 'BGR', stride, 1)
            payload = io.BytesIO()
            image.save(payload, 'JPEG', quality=75)
            elapsed = (time.monotonic_ns() - after) / 1e9
            slow_encodes = slow_encodes + 1 if elapsed > 0.25 else 0
            frame = {'jpeg_base64': base64.b64encode(payload.getvalue()).decode('ascii'),
                     'width': width, 'height': height,
                     'receive_start_ns': before, 'receive_end_ns': after,
                     'frame_id': None, 'exposure_timestamp_ns': None,
                     'timing': 'receive interval only; exact exposure and frame association unavailable',
                     'encode_ms': round(elapsed * 1000, 2)}
            image_state['frames_captured'] += 1
            image_state['last_receive_ns'] = after
            image_state['state'] = 'receiving'
            try:
                image_queue.put_nowait(frame)
            except queue.Full:
                image_state['frames_dropped'] += 1
            if slow_encodes >= 3:
                image_state['state'] = 'paused_due_to_encoding_cost'
                return
            stop_images.wait(max(0.0, 0.5 - (time.monotonic_ns() - before) / 1e9))
    except Exception as exc:
        image_state['state'] = 'unavailable'
        image_state['error'] = '%s: %s' % (type(exc).__name__, exc)


p = Params()
params, param_errors = {}, {}
for key in PARAM_KEYS:
    try:
        params[key] = p.get(key, encoding='utf8')
    except Exception as exc:
        param_errors[key] = '%s: %s' % (type(exc).__name__, exc)
cp_raw = p.get('CarParams')
cp_info = {}
if cp_raw:
    cp = car.CarParams.from_bytes(cp_raw)
    cp_info = select(cp, 'carFingerprint mdpsBus sccBus sasBus steerActuatorDelay steerRatio wheelbase mass centerToFront tireStiffnessFront tireStiffnessRear openpilotLongitudinalControl smoothSteer lateralTuning'.split())
    cp_info['eps_firmware_hex'] = [bytes(f.fwVersion).hex() for f in cp.carFw if str(f.ecu) == 'eps']
emit('metadata', {
    'capture_format': 'geometry-v2', 'read_only': True,
    'wall_time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip(),
    'params': params, 'parameter_read_errors': param_errors, 'car_params': cp_info,
    'seconds_limit': SECONDS, 'can_ids': sorted(CAN_IDS),
    'missing_services': sorted(set(REQUESTED) - set(SERVICES)),
    'panda_signature_sha256': hashlib.sha256(p.get('PandaSignatures') or b'').hexdigest(),
    'can_frame_columns': ['address', 'src', 'data_hex'],
    'src_note': 'base bus; +128 transmitted; +192 rejected. TX echo is not EPS actuation confirmation.',
    'eps_output_note': 'MDPS output report has unspecified physical unit in this DBC.',
    'image_note': '2 fps target, road only, no audio or driver camera. RGB JPEG receive times are approximate; native thumbnail frameId/timestampEof are exact publisher metadata.',
    'geometry_note': 'model lane/road boundaries are predictions, not surveyed ground truth; uncertainty is retained.',
    'mpc_note': 'dPathPoints is MPC input, not the solved vehicle trajectory; solved x/y are not published here.',
})
poller = messaging.Poller()
sockets = [messaging.sub_sock(name, poller=poller, conflate=False) for name in SERVICES]
threading.Thread(target=road_images, daemon=True).start()
start = time.monotonic()
next_status = start
emit('collector_started', {'services': SERVICES, 'not_a_driving_clearance': True})
sys.stdout.flush()
try:
    while time.monotonic() - start < SECONDS:
        for sock in poller.poll(50):
            for _ in range(200):
                event = messaging.recv_one_or_none(sock)
                if event is None:
                    break
                service = event.which()
                value = getattr(event, service)
                counts[service] += 1
                last_source[service] = int(event.logMonoTime)
                last_valid[service] = bool(event.valid)
                if service in ('can', 'sendcan'):
                    data = [[int(f.address), int(f.src), bytes(f.dat).hex()]
                            for f in value if int(f.address) in CAN_IDS]
                    if not data:
                        continue
                elif service in FIELDS:
                    data = select(value, FIELDS[service])
                    if service == 'carState':
                        last['car'] = select(value, ['vEgo', 'steeringAngleDeg', 'steeringTorque', 'steeringPressed'])
                    elif service == 'controlsState':
                        lateral = value.lateralControlState
                        tag = lateral.which()
                        data['lateral_type'] = tag
                        data['lateral'] = getattr(lateral, tag).to_dict()
                        last['op_active'] = bool(value.active)
                elif service == 'modelV2':
                    data = select(value, MODEL_FIELDS)
                    last['geometry'] = {
                        'lane_count': len(data.get('laneLines', [])),
                        'road_edge_count': len(data.get('roadEdges', [])),
                        'lane_probabilities': data.get('laneLineProbs', []),
                        'road_edge_stds': data.get('roadEdgeStds', []),
                    }
                elif service == 'carControl':
                    data = select(value, ['enabled', 'active', 'latActive', 'actuators', 'actuatorsOutput'])
                    last['command'] = float(value.actuators.steer)
                    last['host_final'] = float(value.actuatorsOutput.steer)
                elif service == 'pandaStates':
                    data = [select(v, 'controlsAllowed blockedCnt canSendErrs canRxErrs safetyModel safetyParam heartbeatLost faultStatus faults uptime'.split()) for v in value]
                elif service == 'thumbnail':
                    data = select(value, ['frameId', 'timestampEof'])
                    data['jpeg_base64'] = base64.b64encode(bytes(value.thumbnail)).decode('ascii')
                else:
                    continue
                emit(service, data, event.logMonoTime, event.valid)
        for _ in range(2):
            try:
                frame = image_queue.get_nowait()
            except queue.Empty:
                break
            emit('road_image', frame)
            image_state['frames_emitted'] += 1
        now = time.monotonic()
        if now >= next_status:
            now_ns = time.monotonic_ns()
            ages = {name: round((now_ns - ns) / 1e6, 1) for name, ns in last_source.items()}
            required = ['can', 'carState', 'carControl', 'controlsState', 'modelV2',
                        'lateralPlan', 'liveLocationKalman', 'liveCalibration', 'roadCameraState']
            missing = [name for name in required if name not in ages or ages[name] > 1500]
            geometry = last.get('geometry', {})
            images_recent = image_state['last_receive_ns'] is not None and now_ns - image_state['last_receive_ns'] < 2_000_000_000
            capture_inputs_present = not missing and images_recent and image_state['frames_emitted'] > 0 and geometry.get('lane_count', 0) >= 2 and geometry.get('road_edge_count', 0) >= 2
            emit('status', {'elapsed_s': round(now - start, 1), 'counts': dict(counts),
                            'last': last, 'ages_ms': ages, 'valid': last_valid,
                            'images': dict(image_state), 'missing_or_stale': missing,
                            'capture_inputs_present': capture_inputs_present,
                            'not_a_driving_clearance': True})
            sys.stdout.flush()
            next_status = now + 5
    emit('end', {'reason': 'duration_limit', 'elapsed_s': time.monotonic() - start,
                 'counts': dict(counts), 'images': dict(image_state)})
    sys.stdout.flush()
finally:
    stop_images.set()
