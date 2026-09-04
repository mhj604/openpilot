import math
from collections import deque

from cereal import log
from common.filter_simple import FirstOrderFilter
from common.numpy_fast import clip, interp
from common.realtime import DT_CTRL
from selfdrive.controls.lib.latcontrol import LatControl, MIN_STEER_SPEED
from selfdrive.controls.lib.pid import PIDController
from selfdrive.controls.lib.drive_helpers import apply_deadzone
from selfdrive.controls.lib.vehicle_model import ACCELERATION_DUE_TO_GRAVITY
from selfdrive.car.hyundai.values import CAR

from common.params import Params
from decimal import Decimal

# At higher speeds (25+mph) we can assume:
# Lateral acceleration achieved by a specific car correlates to
# torque applied to the steering rack. It does not correlate to
# wheel slip, or to speed.

# This controller applies torque to achieve desired lateral
# accelerations. To compensate for the low speed effects we
# use a LOW_SPEED_FACTOR in the error. Additionally, there is
# friction in the steering wheel that needs to be overcome to
# move it at all, this is compensated for too.


FRICTION_THRESHOLD = 0.2

# Backported from the current openpilot torque controller. The Grandeur keeps
# its existing normalized torque mapping, while using the upstream controller's
# speed-dependent proportional response and time-aligned lateral acceleration.
KP = 0.8
INTERP_SPEEDS = [1.0, 1.5, 2.0, 3.0, 5.0, 7.5, 10.0, 15.0, 30.0]
KP_INTERP = [250.0, 120.0, 65.0, 30.0, 11.5, 5.5, 3.5, 2.0, KP]
LP_FILTER_CUTOFF_HZ = 1.2
JERK_LOOKAHEAD_SECONDS = 0.19
JERK_GAIN = 0.3
LAT_ACCEL_REQUEST_BUFFER_SECONDS = 1.0


class LatControlTorque(LatControl):
  def __init__(self, CP, CI):
    super().__init__(CP, CI)
    self.CP = CP

    self.mpc_frame = 0
    self.params = Params()

    self.modern_torque_control = CP.carFingerprint == CAR.GRANDEUR_HEV_IG and CP.sccBus == -1
    self.max_lat_accel = float(Decimal(self.params.get("TorqueMaxLatAccel", encoding="utf8")) * Decimal('0.1'))
    self.kp = CP.lateralTuning.torque.kp
    self.ki = CP.lateralTuning.torque.ki
    self.kf = CP.lateralTuning.torque.kf
    self._configure_pid()
    self.get_steer_feedforward = CI.get_steer_feedforward_function()
    self.use_steering_angle = CP.lateralTuning.torque.useSteeringAngle
    self.friction = CP.lateralTuning.torque.friction
    self.steering_angle_deadzone_deg = CP.lateralTuning.torque.steeringAngleDeadzoneDeg

    self.lat_accel_request_buffer_len = max(2, int(LAT_ACCEL_REQUEST_BUFFER_SECONDS / DT_CTRL))
    self.lat_accel_request_buffer = deque([0.0] * self.lat_accel_request_buffer_len,
                                          maxlen=self.lat_accel_request_buffer_len)
    self.lookahead_frames = int(JERK_LOOKAHEAD_SECONDS / DT_CTRL)
    self.jerk_filter = FirstOrderFilter(0.0, 1.0 / (2.0 * math.pi * LP_FILTER_CUTOFF_HZ), DT_CTRL)

    self.live_tune_enabled = False

    self.lt_timer = 0

  def _configure_pid(self):
    if getattr(self, 'modern_torque_control', False):
      # The upstream controller runs its PID in lateral-acceleration space and
      # converts the complete result to normalized steering torque afterwards.
      kp_scale = (self.kp * self.max_lat_accel) / KP
      speed_kp = [gain * kp_scale for gain in KP_INTERP]
      kp = [INTERP_SPEEDS, speed_kp]
      ki = self.ki * self.max_lat_accel
      torque_per_lat_accel = max(self.kf, 1e-3)
      pid_limit = self.steer_max / torque_per_lat_accel
      pid_kf = 1.0
    else:
      kp = self.kp
      ki = self.ki
      pid_limit = self.steer_max
      pid_kf = self.kf
    self.pid = PIDController(kp, ki, k_f=pid_kf,
                             pos_limit=pid_limit, neg_limit=-pid_limit)

  def reset(self):
    super().reset()
    if getattr(self, 'modern_torque_control', False):
      if hasattr(self, 'pid'):
        self.pid.reset()
      if hasattr(self, 'lat_accel_request_buffer'):
        self.lat_accel_request_buffer.clear()
        self.lat_accel_request_buffer.extend([0.0] * self.lat_accel_request_buffer_len)
        self.jerk_filter.x = 0.0

  def live_tune(self, CP):
    self.mpc_frame += 1
    if self.mpc_frame % 300 == 0:
      self.max_lat_accel = float(Decimal(self.params.get("TorqueMaxLatAccel", encoding="utf8")) * Decimal('0.1'))
      self.kp = float(Decimal(self.params.get("TorqueKp", encoding="utf8")) * Decimal('0.1')) / self.max_lat_accel
      self.kf = float(Decimal(self.params.get("TorqueKf", encoding="utf8")) * Decimal('0.1')) / self.max_lat_accel
      self.ki = float(Decimal(self.params.get("TorqueKi", encoding="utf8")) * Decimal('0.1')) / self.max_lat_accel
      self.friction = float(Decimal(self.params.get("TorqueFriction", encoding="utf8")) * Decimal('0.001'))
      self.use_steering_angle = self.params.get_bool('TorqueUseAngle')
      self.steering_angle_deadzone_deg = float(Decimal(self.params.get("TorqueAngDeadZone", encoding="utf8")) * Decimal('0.1'))
      self._configure_pid()
        
      self.mpc_frame = 0

  def update(self, active, CS, CP, VM, params, last_actuators, desired_curvature, desired_curvature_rate, llk):
    modern_torque_control = getattr(self, 'modern_torque_control', False)
    self.lt_timer += 1
    if self.lt_timer > 100:
      self.lt_timer = 0
      self.live_tune_enabled = self.params.get_bool("OpkrLiveTunePanelEnable")
    if self.live_tune_enabled:
      self.live_tune(CP)

    pid_log = log.ControlsState.LateralTorqueState.new_message()

    if CS.vEgo < MIN_STEER_SPEED or not active:
      output_torque = 0.0
      pid_log.active = False
    else:
      if self.use_steering_angle or modern_torque_control:
        actual_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
        curvature_deadzone = abs(VM.calc_curvature(math.radians(self.steering_angle_deadzone_deg), CS.vEgo, 0.0))
      else:
        actual_curvature_vm = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
        actual_curvature_llk = llk.angularVelocityCalibrated.value[2] / CS.vEgo
        actual_curvature = interp(CS.vEgo, [2.0, 5.0], [actual_curvature_vm, actual_curvature_llk])
        curvature_deadzone = 0.0
      actual_lateral_accel = actual_curvature * CS.vEgo ** 2
      lateral_accel_deadzone = curvature_deadzone * CS.vEgo ** 2

      future_desired_lateral_accel = desired_curvature * CS.vEgo ** 2
      if modern_torque_control:
        self.lat_accel_request_buffer.append(future_desired_lateral_accel)
        delay_frames = int(clip(CP.steerActuatorDelay / DT_CTRL + 1,
                                1, self.lat_accel_request_buffer_len))
        setpoint = self.lat_accel_request_buffer[-delay_frames]
        measurement = actual_lateral_accel

        lookahead_idx = int(clip(-delay_frames + self.lookahead_frames,
                                -self.lat_accel_request_buffer_len + 1, -2))
        raw_lateral_jerk = (self.lat_accel_request_buffer[lookahead_idx + 1] -
                            self.lat_accel_request_buffer[lookahead_idx - 1]) / (2.0 * DT_CTRL)
        desired_lateral_jerk = self.jerk_filter.update(raw_lateral_jerk)
      else:
        low_speed_factor = interp(CS.vEgo, [0, 10, 20], [500, 500, 200])
        setpoint = future_desired_lateral_accel + low_speed_factor * desired_curvature
        measurement = actual_lateral_accel + low_speed_factor * actual_curvature
        desired_lateral_jerk = 0.0

      error = setpoint - measurement
      pid_log.error = error

      ff = future_desired_lateral_accel - params.roll * ACCELERATION_DUE_TO_GRAVITY
      # convert friction into lateral accel units for feedforward
      friction_error = error + JERK_GAIN * desired_lateral_jerk
      friction_compensation = interp(apply_deadzone(friction_error, lateral_accel_deadzone), [-FRICTION_THRESHOLD, FRICTION_THRESHOLD], [-self.friction, self.friction])
      ff += friction_compensation / self.kf
      freeze_integrator = CS.steeringRateLimited or CS.steeringPressed or CS.vEgo < 5
      controller_output = self.pid.update(error,
                                          feedforward=ff,
                                          speed=CS.vEgo,
                                          freeze_integrator=freeze_integrator)
      output_torque = clip(controller_output * self.kf, -self.steer_max, self.steer_max) if modern_torque_control else controller_output

      pid_log.active = True
      pid_log.p = self.pid.p
      pid_log.i = self.pid.i
      pid_log.d = self.pid.d
      pid_log.f = self.pid.f
      pid_log.output = -output_torque
      pid_log.saturated = self._check_saturation(self.steer_max - abs(output_torque) < 1e-3, CS)
      pid_log.actualLateralAccel = actual_lateral_accel
      pid_log.desiredLateralAccel = setpoint

    # TODO left is positive in this convention
    return -output_torque, 0.0, pid_log
