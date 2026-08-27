#!/usr/bin/env python3
import ast
import ipaddress
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cereal.messaging as messaging
from common.basedir import BASEDIR
from common.params import Params


PORT = 8086
WEB_ROOT = Path(__file__).resolve().parent
INDEX_FILE = WEB_ROOT / "index.html"
SOURCE_ROOT = Path(BASEDIR)
PARAMS_DIR = Path("/data/params/d")

PANEL_CLASSES = {
  "화면/UI": [
    "AutoShutdown", "ForceShutdown", "VolumeControl", "BrightnessControl",
    "AutoScreenOff", "BrightnessOffControl", "DoNotDisturbMode", "GetOffAlert",
    "BatteryChargingControlToggle", "ChargingMin", "ChargingMax",
    "DrivingRecordToggle", "RecordCount", "RecordQuality", "MonitoringMode",
    "MonitorEyesThreshold", "NormalEyesThreshold", "BlinkThreshold",
    "OPKRNaviSelect", "ExternalDeviceIP", "RunNaviOnBootToggle",
    "OPKRServerSelect", "OPKRServerAPI", "MapboxEnabledToggle", "OPKRMapboxStyle",
    "GoogleMapEnabledToggle", "OPKRTopTextView", "RPMAnimatedToggle",
    "RPMAnimatedMaxValue", "ShowStopLineToggle", "HoldForSettingToggle",
    "RTShieldToggle", "OSMOfflineUseToggle",
  ],
  "주행": [
    "AutoResumeToggle", "RESCountatStandstill", "CruiseGapAdjustToggle",
    "CruiseGapBySpdOn", "CruiseGapBySpd", "StandstillResumeAltToggle",
    "DepartChimeAtResume", "VariableCruiseToggle", "VariableCruiseLevel",
    "CruiseSetwithRoadLimitSpeed", "CruiseSetwithRoadLimitSpeedOffset",
    "CruisemodeSelInit", "LaneChangeSpeed", "LaneChangeDelay", "LCTimingFactorUD",
    "LCTimingFactor", "LeftCurvOffset", "RightCurvOffset", "BlindSpotDetectToggle",
    "CSteerWidget", "SteerAngleCorrection", "TurnSteeringDisableToggle",
    "CruiseOverMaxSpeedToggle", "OSMEnabledToggle", "OSMSpeedLimitEnabledToggle",
    "StockNaviSpeedToggle", "SpeedLimitOffset", "OSMCustomSpeedLimitUD",
    "OSMCustomSpeedLimit", "SpeedLimitSignType", "CamDecelDistAdd",
    "CurvDecelSelect", "VCurvSpeedUD", "VCurvSpeed", "OCurvSpeedUD", "OCurvSpeed",
    "SpeedBumpDecelToggle", "OPKREarlyStoppingToggle", "AutoEnabledToggle",
    "AutoEnableSpeed", "CruiseAutoResToggle", "RESChoice", "AutoResCondition",
    "AutoResLimitTime", "AutoRESDelay", "LaneWidth", "SpeedLaneWidthUD",
    "SpeedLaneWidth", "RoutineDriveOnToggle", "RoutineDriveOption",
    "CloseToRoadEdgeToggle", "OPKREdgeOffset", "ToAvoidLKASFaultToggle",
    "ToAvoidLKASFault", "SpeedCameraOffsetToggle",
  ],
  "개발자": [
    "DebugUiOneToggle", "DebugUiTwoToggle", "DebugUiThreeToggle", "OPKRDebug",
    "ShowErrorToggle", "LongLogToggle", "PrebuiltToggle", "FPTwoToggle",
    "WhitePandaSupportToggle", "BattLessToggle", "ComIssueToggle", "LDWSToggle",
    "GearDToggle", "SteerWarningFixToggle", "IgnoreCanErroronISGToggle",
    "FCA11MessageToggle", "UFCModeEnabledToggle",
    "StockLKASEnabledatDisenagedStatusToggle", "C2WithCommaPowerToggle",
    "JoystickModeToggle", "NoSmartMDPSToggle", "UserSpecificFeature",
    "TimeZoneSelectCombo", "CarSelectCombo", "CPandaGroup",
  ],
  "튜닝": [
    "CameraOffset", "PathOffset", "SteerActuatorDelay", "TireStiffnessFactor",
    "SteerThreshold", "SteerLimitTimer", "LiveSteerRatioToggle", "LiveSRPercent",
    "SRBaseControl", "SRMaxControl", "VariableSteerMaxToggle", "SteerMax",
    "VariableSteerDeltaToggle", "SteerDeltaUp", "SteerDeltaDown",
    "ToAvoidLKASFaultBeyondToggle", "DesiredCurvatureLimit",
    "LiveTunePanelToggle", "CLateralControlGroup", "CLongControlGroup",
  ],
}

BASE_TOGGLES = [
  ("OpenpilotEnabledToggle", "Enable openpilot", "openpilot 주행 보조 기능을 사용합니다."),
  ("IsLdwEnabled", "Enable Lane Departure Warnings", "차선 이탈 경고를 사용합니다."),
  ("IsRHD", "Enable Right-Hand Drive", "우핸들 차량 설정을 사용합니다."),
  ("IsMetric", "Use Metric System", "속도와 거리를 미터법으로 표시합니다."),
  ("RecordFront", "Record and Upload Driver Camera", "운전자 카메라 영상을 기록합니다."),
  ("EndToEndToggle", "Enable Lane selector Mode", "차선 선택 모드를 사용합니다."),
  ("OpkrEnableLogger", "Enable Driving Log Record", "주행 로그를 장치에 저장합니다."),
  ("OpkrEnableUploader", "Enable Sending Log to Server", "저장된 로그의 업로드를 사용합니다."),
]

NETWORK_FIELDS = [
  ("WebUIEnabled", "WebUI 사용", "이 설정 화면을 8086 포트에서 제공합니다."),
  ("OpkrHotspotOnBoot", "HotSpot on Boot", "부팅할 때 핫스팟을 자동으로 켭니다."),
  ("SshEnabled", "Enable SSH", "SSH 원격 접속을 허용합니다."),
  ("OpkrSSHLegacy", "Use Legacy SSH Key", "구형 공개키 방식의 SSH 접속을 사용합니다."),
]

READ_ONLY_KEYS = {
  "DongleId", "HardwareSerial", "Version", "GitRemote", "GitBranch", "GitCommit",
  "GitCommitRemote", "LastUpdateTime", "LastAthenaPingTime", "IsOffroad", "IsOnroad",
}

NEVER_EXPOSE_KEYS = {
  "AccessToken", "SubscriberInfo", "CarParams", "CarParamsCache", "CalibrationParams",
  "LiveParameters", "PandaFirmware", "PandaFirmwareHex", "PandaSignatures", "GitDiff",
  "ApiCache_Device", "ApiCache_DriveStats", "ApiCache_NavDestinations", "ApiCache_Owner",
}

BOOL_HINTS = {
  "OpenpilotEnabledToggle", "IsLdwEnabled", "IsRHD", "IsMetric", "RecordFront",
  "EndToEndToggle", "SshEnabled", "WebUIEnabled",
}

# Additional help for values whose raw Params representation differs from the
# value used by controls. Existing Qt descriptions/translations are used first.
PARAM_HELP: Dict[str, Dict[str, str]] = {
  # Driving
  "OpkrAutoResume": {"description": "순정 SCC 정차 상태에서 자동으로 RES 신호를 보내 출발합니다."},
  "RESCountatStandstill": {"description": "정차 후 자동 출발 시 전송할 RES 버튼 메시지 횟수입니다.", "unit": "count"},
  "CruiseGapAdjust": {"description": "정차 시 크루즈 차간거리 단계를 자동으로 줄였다가 출발 후 복원합니다."},
  "CruiseGapBySpdOn": {"description": "차량 속도 구간에 따라 순정 크루즈 차간거리 단계를 변경합니다."},
  "CruiseGapBySpdSpd": {"description": "차간거리 단계를 바꿀 속도 경계값 목록입니다.", "unit": "km/h", "hint": "쉼표로 구분"},
  "CruiseGapBySpdGap": {"description": "각 속도 구간에 적용할 차간거리 단계 목록입니다.", "unit": "level", "hint": "쉼표로 구분"},
  "StandstillResumeAlt": {"description": "일반 자동 출발이 동작하지 않는 차량에서 대체 RES 전송 방식을 사용합니다."},
  "DepartChimeAtResume": {"description": "RES로 출발할 때 알림음을 재생합니다."},
  "OpkrVariableCruise": {"description": "순정 크루즈 버튼을 자동 전송해 목표 속도에 맞춰 가감속을 보조합니다."},
  "VarCruiseSpeedFactor": {"description": "가변 크루즈 속도 보정 강도입니다.", "unit": "%"},
  "CruiseSetwithRoadLimitSpeedEnabled": {"description": "도로 제한속도를 기준으로 크루즈 설정속도를 맞춥니다."},
  "CruiseSetwithRoadLimitSpeedOffset": {"description": "도로 제한속도에 더할 크루즈 설정속도 보정값입니다.", "unit": "km/h"},
  "CruiseStatemodeSelInit": {"description": "크루즈 모드의 부팅 시 기본 선택값입니다.", "unit": "enum"},
  "OpkrLaneChangeSpeed": {"description": "자동 차선 변경을 허용하는 최저 차량 속도입니다.", "unit": "km/h"},
  "OpkrAutoLaneChangeDelay": {"description": "방향지시등 입력 후 자동 차선 변경을 시작하기까지의 지연 설정입니다.", "unit": "step"},
  "LCTimingFactorEnable": {"description": "속도별 차선 변경 타이밍 보정값을 사용합니다."},
  "LCTimingFactor30": {"description": "30km/h 부근 차선 변경 타이밍 배율입니다.", "unit": "ratio", "hint": "raw × 0.01"},
  "LCTimingFactor60": {"description": "60km/h 부근 차선 변경 타이밍 배율입니다.", "unit": "ratio", "hint": "raw × 0.01"},
  "LCTimingFactor80": {"description": "80km/h 부근 차선 변경 타이밍 배율입니다.", "unit": "ratio", "hint": "raw × 0.01"},
  "LCTimingFactor110": {"description": "110km/h 부근 차선 변경 타이밍 배율입니다.", "unit": "ratio", "hint": "raw × 0.01"},
  "LeftCurvOffsetAdj": {"description": "좌회전 곡선에서 경로를 좌우로 보정하는 값입니다.", "unit": "offset"},
  "RightCurvOffsetAdj": {"description": "우회전 곡선에서 경로를 좌우로 보정하는 값입니다.", "unit": "offset"},
  "OpkrSteerAngleCorrection": {"description": "차량에서 수신한 조향각의 영점을 보정합니다.", "unit": "deg", "hint": "raw × 0.1 = deg"},
  "OpkrTurnSteeringDisable": {"description": "저속에서 방향지시등을 켜면 조향 보조를 일시 정지합니다."},
  "CruiseOverMaxSpeed": {"description": "현재 속도가 설정속도를 넘으면 설정속도를 현재 속도에 맞춥니다."},
  "OSMEnable": {"description": "OpenStreetMap 도로 정보를 사용합니다."},
  "OSMSpeedLimitEnable": {"description": "OpenStreetMap의 도로 제한속도 정보를 사용합니다."},
  "StockNaviSpeedEnabled": {"description": "차량 순정 내비게이션의 안전구간 속도 정보를 사용합니다."},
  "OpkrSpeedLimitOffset": {"description": "제한속도 제어에 추가할 보정값입니다.", "unit": "km/h or %"},
  "OSMCustomSpeedLimitC": {"description": "OSM 제한속도 보정의 입력 속도 구간입니다.", "unit": "km/h", "hint": "쉼표로 구분"},
  "OSMCustomSpeedLimitT": {"description": "각 OSM 속도 구간에 적용할 목표 속도입니다.", "unit": "km/h", "hint": "쉼표로 구분"},
  "SafetyCamDecelDistGain": {"description": "안전카메라 감속을 시작하는 거리에 더할 보정값입니다.", "unit": "distance offset"},
  "CurvDecelOption": {"description": "곡률 감속에 사용할 데이터 조합을 선택합니다.", "unit": "enum"},
  "VCurvSpeedC": {"description": "비전 곡률 감속의 기준 곡률 목록입니다.", "unit": "curvature index", "hint": "쉼표로 구분"},
  "VCurvSpeedT": {"description": "비전 곡률별 목표 속도 목록입니다.", "unit": "km/h", "hint": "쉼표로 구분"},
  "OCurvSpeedC": {"description": "OSM 곡률 감속의 기준 곡률 목록입니다.", "unit": "curvature index", "hint": "쉼표로 구분"},
  "OCurvSpeedT": {"description": "OSM 곡률별 목표 속도 목록입니다.", "unit": "km/h", "hint": "쉼표로 구분"},
  "OPKRSpeedBump": {"description": "과속방지턱 구간에서 목표 속도를 낮춥니다."},
  "OPKREarlyStop": {"description": "선행차 정지 상황에서 차간거리 신호를 이용해 더 일찍 감속합니다."},
  "AutoEnable": {"description": "크루즈 대기 상태에서 조건이 맞으면 OP를 자동 활성화합니다."},
  "AutoEnableSpeed": {"description": "자동 OP 활성화를 허용할 속도 기준입니다.", "unit": "km/h or mode"},
  "CruiseAutoRes": {"description": "주행 중 브레이크로 크루즈가 대기 상태가 되면 자동으로 RES를 수행합니다."},
  "AutoResOption": {"description": "자동 RES가 이전 설정속도를 복원하는 방식을 선택합니다.", "unit": "enum"},
  "AutoResCondition": {"description": "자동 RES를 시작할 페달 또는 차량 조건을 선택합니다.", "unit": "enum"},
  "AutoResLimitTime": {"description": "브레이크 해제 후 자동 RES를 허용할 제한시간입니다.", "unit": "s"},
  "AutoRESDelay": {"description": "자동 RES 실행 전 대기시간입니다.", "unit": "s"},
  "LaneWidth": {"description": "차선 모델이 사용할 기본 차로 폭입니다.", "unit": "m", "hint": "raw × 0.1 = m"},
  "SpdLaneWidthSpd": {"description": "속도별 차로 폭을 전환할 속도 구간입니다.", "unit": "km/h", "hint": "쉼표로 구분"},
  "SpdLaneWidthSet": {"description": "각 속도 구간에서 사용할 차로 폭입니다.", "unit": "m", "hint": "쉼표로 구분"},
  "RoutineDriveOn": {"description": "도로명에 따라 오프셋과 제한속도 설정을 자동 적용합니다."},
  "RoutineDriveOption": {"description": "도로명 기반 루틴 주행에 사용할 공급자를 선택합니다.", "unit": "enum"},
  "CloseToRoadEdge": {"description": "가장자리 차로에서 차량 경로를 도로 가장자리 쪽으로 보정합니다."},
  "LeftEdgeOffset": {"description": "왼쪽 도로 가장자리에서 사용할 경로 보정값입니다.", "unit": "m"},
  "RightEdgeOffset": {"description": "오른쪽 도로 가장자리에서 사용할 경로 보정값입니다.", "unit": "m"},
  "AvoidLKASFaultEnabled": {"description": "차량별 최대 조향각을 넘지 않도록 LKAS 명령을 제한합니다."},
  "AvoidLKASFaultMaxAngle": {"description": "LKAS 오류 방지를 시작할 최대 조향각입니다.", "unit": "deg"},
  "AvoidLKASFaultMaxFrame": {"description": "최대 조향각 상태를 허용할 CAN 프레임 수입니다.", "unit": "frame"},
  "SpeedCameraOffset": {"description": "속도에 따라 카메라 오프셋을 추가 보정합니다."},

  # Tuning
  "CameraOffsetAdj": {"description": "차량 중심 대비 카메라 위치를 좌우로 보정합니다. 양수는 왼쪽, 음수는 오른쪽입니다.", "unit": "m", "hint": "raw × 0.001 = m"},
  "PathOffsetAdj": {"description": "계획 경로 전체를 좌우로 이동합니다. 양수는 왼쪽, 음수는 오른쪽입니다.", "unit": "m", "hint": "raw × 0.001 = m"},
  "SteerActuatorDelayAdj": {"description": "조향 명령부터 실제 차량 반응까지의 지연시간입니다.", "unit": "s", "hint": "raw × 0.01 = s"},
  "TireStiffnessFactorAdj": {"description": "차량 모델의 타이어 횡강성 배율입니다.", "unit": "ratio", "hint": "raw × 0.01"},
  "SteerThreshold": {"description": "운전자 조향 개입으로 판단할 토크 기준값입니다.", "unit": "torque count"},
  "SteerLimitTimerAdj": {"description": "조향 출력 제한 상태를 허용하는 시간입니다.", "unit": "s", "hint": "raw × 0.01 = s"},
  "OpkrLiveSteerRatio": {"description": "학습된 실시간 조향비를 차량 모델에 사용합니다."},
  "LiveSteerRatioPercent": {"description": "학습된 실시간 조향비에 추가할 비율 보정입니다.", "unit": "%"},
  "SteerRatioAdj": {"description": "차량 모델이 사용할 기본 조향비입니다.", "unit": "ratio", "hint": "raw × 0.01"},
  "SteerRatioMaxAdj": {"description": "가변 조향비가 올라갈 수 있는 최대값입니다.", "unit": "ratio", "hint": "raw × 0.01"},
  "OpkrVariableSteerMax": {"description": "모델 곡률에 따라 최대 조향 출력을 가변 적용합니다."},
  "SteerMaxBaseAdj": {"description": "기본 최대 조향 출력값입니다.", "unit": "Panda torque count"},
  "SteerMaxAdj": {"description": "가변 제어에서 허용할 최대 조향 출력값입니다.", "unit": "Panda torque count"},
  "OpkrVariableSteerDelta": {"description": "모델 곡률에 따라 조향 출력 변화율을 가변 적용합니다."},
  "SteerDeltaUpBaseAdj": {"description": "기본 조향 출력 증가 제한값입니다.", "unit": "count/frame"},
  "SteerDeltaUpAdj": {"description": "가변 제어의 최대 조향 출력 증가 제한값입니다.", "unit": "count/frame"},
  "SteerDeltaDownBaseAdj": {"description": "기본 조향 출력 감소 제한값입니다.", "unit": "count/frame"},
  "SteerDeltaDownAdj": {"description": "가변 제어의 최대 조향 출력 감소 제한값입니다.", "unit": "count/frame"},
  "AvoidLKASFaultBeyond": {"description": "추가 조향 설정을 사용할 때 LKAS 오류 방지 범위를 확장합니다."},
  "DesiredCurvatureLimit": {"description": "목표 곡률 변화율에 적용할 제한 배율입니다.", "unit": "ratio", "hint": "raw × 0.01"},
  "OpkrLiveTunePanelEnable": {"description": "지원되는 횡제어 게인과 오프셋을 주행 프로세스가 주기적으로 다시 읽도록 합니다."},
  "LateralControlMethod": {"description": "사용할 횡방향 제어기(PID, INDI, LQR, Torque)를 선택합니다.", "unit": "enum"},
  "PidKp": {"description": "PID 횡제어의 비례 게인입니다.", "unit": "gain", "hint": "raw × 0.01"},
  "PidKi": {"description": "PID 횡제어의 적분 게인입니다.", "unit": "gain", "hint": "raw × 0.001"},
  "PidKd": {"description": "PID 횡제어의 미분 게인입니다.", "unit": "gain", "hint": "raw × 0.01"},
  "PidKf": {"description": "PID 횡제어의 피드포워드 게인입니다.", "unit": "gain", "hint": "raw × 0.00001"},
  "TorqueKp": {"description": "토크 횡제어의 비례 게인입니다.", "unit": "gain", "hint": "raw × 0.1 ÷ max lateral accel"},
  "TorqueKi": {"description": "토크 횡제어의 적분 게인입니다.", "unit": "gain", "hint": "raw × 0.1 ÷ max lateral accel"},
  "TorqueKf": {"description": "토크 횡제어의 피드포워드 게인입니다.", "unit": "gain", "hint": "raw × 0.1 ÷ max lateral accel"},
  "TorqueFriction": {"description": "조향계 마찰을 보상할 토크값입니다.", "unit": "torque", "hint": "raw × 0.001"},
  "TorqueMaxLatAccel": {"description": "토크 제어 정규화에 사용할 최대 횡가속도입니다.", "unit": "m/s²", "hint": "raw × 0.1"},
  "TorqueAngDeadZone": {"description": "토크 제어에서 무시할 조향각 오차 범위입니다.", "unit": "deg", "hint": "raw × 0.1 = deg"},
  "TorqueUseAngle": {"description": "토크 제어의 실제 곡률 계산에 조향각 센서를 사용합니다."},
  "InnerLoopGain": {"description": "INDI 제어기의 내부 루프 게인입니다.", "unit": "gain", "hint": "raw × 0.1"},
  "OuterLoopGain": {"description": "INDI 제어기의 외부 루프 게인입니다.", "unit": "gain", "hint": "raw × 0.1"},
  "TimeConstant": {"description": "INDI 액추에이터 모델의 시간상수입니다.", "unit": "s", "hint": "raw × 0.1 = s"},
  "ActuatorEffectiveness": {"description": "INDI 모델의 조향 액추에이터 효과 계수입니다.", "unit": "factor", "hint": "raw × 0.1"},
  "Scale": {"description": "LQR 제어기의 전체 출력 스케일입니다.", "unit": "scale"},
  "LqrKi": {"description": "LQR 제어기의 적분 게인입니다.", "unit": "gain", "hint": "raw × 0.001"},
  "DcGain": {"description": "LQR 제어기의 정상상태 DC 게인입니다.", "unit": "gain", "hint": "raw × 0.00001"},
  "StoppingDist": {"description": "종방향 제어가 정지 단계에 진입하는 선행차 거리 기준입니다.", "unit": "m", "hint": "raw × 0.1 = m"},
  "StoppingDistAdj": {"description": "정지 시 레이더 기준거리보다 여유 있게 멈추도록 정지거리를 보정합니다."},
}

LIVE_1S_KEYS = {"CameraOffsetAdj", "PathOffsetAdj", "OpkrLiveSteerRatio", "LiveSteerRatioPercent", "SpeedLimitDecelOff"}
LIVE_3S_KEYS = {"PidKp", "PidKi", "PidKd", "PidKf", "TorqueKp", "TorqueKi", "TorqueKf", "TorqueFriction", "TorqueMaxLatAccel", "TorqueAngDeadZone", "TorqueUseAngle", "InnerLoopGain", "OuterLoopGain", "TimeConstant", "ActuatorEffectiveness", "Scale", "LqrKi", "DcGain"}
REBOOT_KEYS = {"LateralControlMethod", "OSMEnable", "OSMSpeedLimitEnable", "MapboxEnabled", "RTShield", "C2WithCommaPower", "OPKRNaviSelect"}


def infer_unit(key: str, field_kind: str) -> str:
  if field_kind == "bool":
    return "ON / OFF"
  if field_kind == "text":
    return "text"
  if re.search(r"(?:Speed|Spd)$|SpeedC$|SpeedT$", key):
    return "km/h"
  if re.search(r"Angle|Ang", key):
    return "deg"
  if re.search(r"Percent|Offset$", key):
    return "%"
  if re.search(r"Count", key):
    return "count"
  if re.search(r"Delay|Time|Timer|Wait", key):
    return "time"
  if re.search(r"MaxFrame|Frame", key):
    return "frame"
  return "value"


def infer_description(key: str, title: str, field_kind: str) -> str:
  if field_kind == "bool":
    return f"{title} 기능을 켜거나 끕니다."
  if key.endswith(("Spd", "Speed")):
    return f"{title}에 사용할 속도 기준값입니다."
  if key.endswith(("Option", "Select", "Method", "Mode")):
    return f"{title}의 동작 방식을 선택하는 번호입니다."
  if "," in key:
    return f"{title} 설정값입니다."
  return f"{title}에 사용하는 설정값입니다."


def infer_apply(key: str, read_only: bool) -> str:
  if read_only:
    return "읽기 전용"
  if key in LIVE_1S_KEYS:
    return "LiveTune 약 1초"
  if key in LIVE_3S_KEYS:
    return "LiveTune 약 3초"
  if key in REBOOT_KEYS:
    return "재부팅 필요"
  return "UI 새로고침 권장"


def read_text(path: Path) -> str:
  try:
    return path.read_text(encoding="utf-8")
  except (OSError, UnicodeDecodeError):
    return ""


def load_translations() -> Dict[str, str]:
  translations: Dict[str, str] = {}
  path = SOURCE_ROOT / "selfdrive/ui/translations/main_ko.ts"
  try:
    root = ET.parse(str(path)).getroot()
  except (OSError, ET.ParseError):
    return translations

  for message in root.findall(".//message"):
    source = message.findtext("source")
    translated = message.findtext("translation")
    if source and translated and translated.strip():
      translations[source] = translated.strip()
  return translations


def translated(text: str, translations: Dict[str, str]) -> str:
  return translations.get(text, text)


def load_default_params() -> Dict[str, str]:
  defaults: Dict[str, str] = {}
  source = read_text(SOURCE_ROOT / "selfdrive/manager/manager.py")
  try:
    tree = ast.parse(source)
  except SyntaxError:
    return defaults

  for node in ast.walk(tree):
    values: Optional[List[ast.AST]] = None
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "default_params" for t in node.targets):
      if isinstance(node.value, ast.List):
        values = node.value.elts
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append":
      if isinstance(node.func.value, ast.Name) and node.func.value.id == "default_params":
        values = node.args

    for value in values or []:
      if not isinstance(value, ast.Tuple) or len(value.elts) != 2:
        continue
      key_node, default_node = value.elts
      if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
        continue
      if isinstance(default_node, ast.Constant) and isinstance(default_node.value, (str, bytes)):
        default = default_node.value.decode("utf-8", "replace") if isinstance(default_node.value, bytes) else default_node.value
        defaults[key_node.value] = default
  return defaults


def known_param_keys() -> Set[str]:
  source = read_text(SOURCE_ROOT / "selfdrive/common/params.cc")
  return set(re.findall(r'\{"([A-Za-z0-9_]+)",\s*[A-Z_ |]+\}', source))


def extract_class_metadata() -> Tuple[Dict[str, Dict[str, str]], Dict[str, List[str]], Set[str]]:
  header = read_text(SOURCE_ROOT / "selfdrive/ui/qt/widgets/opkr.h")
  implementation = read_text(SOURCE_ROOT / "selfdrive/ui/qt/widgets/opkr.cc")
  settings = read_text(SOURCE_ROOT / "selfdrive/ui/qt/offroad/settings.cc")
  steer_header = read_text(SOURCE_ROOT / "selfdrive/ui/qt/widgets/steerWidget.h")
  steer_implementation = read_text(SOURCE_ROOT / "selfdrive/ui/qt/widgets/steerWidget.cc")
  sources = (("opkr", implementation), ("steer", steer_implementation))

  metadata: Dict[str, Dict[str, str]] = {}
  class_params: Dict[str, List[str]] = {}
  bool_keys: Set[str] = set(BOOL_HINTS)

  toggle_pattern = re.compile(
    r'class\s+(\w+)\s*:\s*public\s+ToggleControl\s*\{.*?'
    r'\1\s*\([^)]*\)\s*:\s*ToggleControl\(tr\("((?:\\.|[^"\\])*)"\),\s*'
    r'(?:tr\("((?:\\.|[^"\\])*)"\)|"((?:\\.|[^"\\])*)").*?'
    r'getBool\("([A-Za-z0-9_]+)"\)', re.S)
  for match in toggle_pattern.finditer(header):
    class_name, title, desc_tr, desc_plain, key = match.groups()
    metadata[class_name] = {"title": title, "description": desc_tr or desc_plain or ""}
    class_params[class_name] = [key]
    bool_keys.add(key)

  constructor_pattern = re.compile(
    r'(\w+)::\1\s*\([^)]*\)\s*:\s*AbstractControl\(tr\("((?:\\.|[^"\\])*)"\)'
    r'(?:,\s*tr\("((?:\\.|[^"\\])*)"\))?', re.S)
  for match in constructor_pattern.finditer(implementation):
    class_name, title, description = match.groups()
    metadata.setdefault(class_name, {"title": title, "description": description or ""})

  method_start = re.compile(r'^(?:[A-Za-z0-9_:<>,*&]+\s+)*([A-Za-z0-9_]+)::[~A-Za-z0-9_]+\s*\(')
  param_use = re.compile(r'(?:Params\(\)|params)\.(?:getBool|putBool|get|put|remove)\s*\(\s*"([A-Za-z0-9_]+)"')
  bool_use = re.compile(r'(?:Params\(\)|params)\.(?:getBool|putBool)\s*\(\s*"([A-Za-z0-9_]+)"')
  for _, source in sources:
    current_class: Optional[str] = None
    for line in source.splitlines():
      method_match = method_start.match(line)
      if method_match:
        current_class = method_match.group(1)
      if current_class:
        for key in param_use.findall(line):
          class_params.setdefault(current_class, [])
          if key not in class_params[current_class]:
            class_params[current_class].append(key)
        bool_keys.update(bool_use.findall(line))

  # Simple ToggleControl declarations in settings.cc and helper groups.
  bool_keys.update(re.findall(r'getBool\("([A-Za-z0-9_]+)"\)', settings + header + steer_header + steer_implementation))
  return metadata, class_params, bool_keys


def humanize_key(key: str) -> str:
  words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key).replace("_", " ")
  return words.strip()


def field_type(key: str, default: str, bool_keys: Set[str]) -> str:
  if key in bool_keys:
    return "bool"
  try:
    float(default)
    return "number"
  except (TypeError, ValueError):
    return "text"


def build_schema() -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
  defaults = load_default_params()
  translations = load_translations()
  known_keys = known_param_keys()
  metadata, class_params, bool_keys = extract_class_metadata()
  editable_keys = (set(defaults) | {key for keys in class_params.values() for key in keys} | {x[0] for x in BASE_TOGGLES + NETWORK_FIELDS})
  editable_keys &= known_keys
  editable_keys -= READ_ONLY_KEYS | NEVER_EXPOSE_KEYS

  fields: Dict[str, Dict[str, Any]] = {}

  def make_field(key: str, title: str = "", description: str = "", class_name: str = "") -> Optional[Dict[str, Any]]:
    if key not in known_keys or key in NEVER_EXPOSE_KEYS:
      return None
    default = defaults.get(key, "")
    resolved_title = translated(title, translations) if title else humanize_key(key)
    resolved_description = translated(description, translations) if description else ""
    kind = field_type(key, default, bool_keys)
    read_only = key not in editable_keys
    help_info = PARAM_HELP.get(key, {})
    if help_info.get("description"):
      resolved_description = help_info["description"]
    if not resolved_description:
      resolved_description = infer_description(key, resolved_title, kind)
    field = {
      "key": key,
      "title": resolved_title,
      "description": resolved_description,
      "type": kind,
      "default": default,
      "readOnly": read_only,
      "source": class_name,
      "unit": help_info.get("unit", infer_unit(key, kind)),
      "hint": help_info.get("hint", "쉼표로 구분" if "," in default else ""),
      "apply": infer_apply(key, read_only),
    }
    fields[key] = field
    return field

  sections: List[Dict[str, Any]] = []
  device_fields = []
  for key, title in (("DongleId", "동글 ID"), ("HardwareSerial", "시리얼")):
    item = make_field(key, title)
    if item:
      device_fields.append(item)
  sections.append({"id": "device", "title": "장치", "fields": device_fields, "actions": True})

  network = []
  for key, title, description in NETWORK_FIELDS:
    item = make_field(key, title, description)
    if item:
      network.append(item)
  sections.append({"id": "network", "title": "네트워크", "fields": network})

  toggles = []
  for key, title, description in BASE_TOGGLES:
    item = make_field(key, title, description)
    if item:
      toggles.append(item)
  if "DisableRadar" in known_keys:
    item = make_field("DisableRadar", "openpilot Longitudinal Control", "openpilot이 가속과 제동을 제어합니다. AEB가 비활성화될 수 있습니다.")
    if item:
      toggles.append(item)
  sections.append({"id": "toggles", "title": "토글", "fields": toggles})

  software = []
  for key, title in (
    ("Version", "버전"), ("GitRemote", "Git 원격 저장소"), ("GitBranch", "Git 브랜치"),
    ("GitCommit", "Git 커밋"), ("GitCommitRemote", "원격 Git 커밋"),
    ("LastUpdateTime", "마지막 업데이트 확인"),
  ):
    item = make_field(key, title)
    if item:
      software.append(item)
  sections.append({"id": "software", "title": "소프트웨어", "fields": software})

  placed: Set[str] = {f["key"] for section in sections for f in section["fields"]}
  section_ids = {"화면/UI": "ui", "주행": "driving", "개발자": "developer", "튜닝": "tuning"}
  for panel_title, classes in PANEL_CLASSES.items():
    panel_fields = []
    for class_name in classes:
      class_meta = metadata.get(class_name, {})
      params_for_class = class_params.get(class_name, [])
      for index, key in enumerate(params_for_class):
        if key in placed:
          continue
        title = class_meta.get("title", "")
        if len(params_for_class) > 1 and title:
          title = f"{title} · {humanize_key(key)}"
        item = make_field(key, title, class_meta.get("description", ""), class_name)
        if item:
          panel_fields.append(item)
          placed.add(key)
    sections.append({"id": section_ids[panel_title], "title": panel_title, "fields": panel_fields})

  advanced = []
  for key in sorted(editable_keys - placed):
    item = make_field(key)
    if item:
      advanced.append(item)
  sections.append({
    "id": "advanced", "title": "전체 Params", "fields": advanced,
    "description": "C2 manager 기본값과 UI에서 사용하는 모든 설정 키입니다.",
  })
  return sections, fields


SECTIONS, FIELDS = build_schema()


class VehicleActivity:
  def __init__(self) -> None:
    self.lock = threading.Lock()
    self.moving = True
    self.engaged = True
    self.stationary_since = 0.0
    self.last_update = 0.0
    self.thread: Optional[threading.Thread] = None

  def start(self) -> None:
    if self.thread is None or not self.thread.is_alive():
      self.thread = threading.Thread(target=self._run, daemon=True)
      self.thread.start()

  def _run(self) -> None:
    while True:
      try:
        sm = messaging.SubMaster(["carState", "controlsState"])
        while True:
          sm.update(1000)
          updated = False
          with self.lock:
            if sm.updated["carState"]:
              moving = abs(sm["carState"].vEgo) > 0.1
              if moving:
                self.stationary_since = 0.0
              elif self.moving or self.stationary_since == 0.0:
                self.stationary_since = time.monotonic()
              self.moving = moving
              updated = True
            if sm.updated["controlsState"]:
              self.engaged = sm["controlsState"].enabled
              updated = True
            if updated:
              self.last_update = time.monotonic()
      except Exception as error:
        print("webui: vehicle state monitor restarting:", error)
        time.sleep(1)

  def snapshot(self) -> Tuple[bool, bool, float, bool]:
    with self.lock:
      fresh = time.monotonic() - self.last_update < 5.0
      stationary_for = time.monotonic() - self.stationary_since if self.stationary_since > 0.0 else 0.0
      return self.moving, self.engaged, stationary_for, fresh


VEHICLE_ACTIVITY = VehicleActivity()


def decode_param(value: Optional[bytes]) -> str:
  if value is None:
    return ""
  return value.decode("utf-8", "replace").rstrip("\x00")


def is_allowed_client(address: str) -> bool:
  try:
    ip = ipaddress.ip_address(address)
  except ValueError:
    return False
  if ip.is_loopback or ip.is_private:
    return True
  if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
    return True
  return False


def is_offroad(params: Params) -> bool:
  try:
    return params.get_bool("IsOffroad")
  except Exception:
    return False


def modification_status(params: Params) -> Tuple[bool, str]:
  if is_offroad(params):
    return True, "offroad"

  moving, engaged, stationary_for, fresh = VEHICLE_ACTIVITY.snapshot()
  if not fresh:
    return False, "unknown"
  if engaged:
    return False, "engaged"
  if moving:
    return False, "moving"
  if stationary_for < 2.0:
    return False, "settling"
  return True, "stopped"


def current_values(params: Params) -> Dict[str, str]:
  values: Dict[str, str] = {}
  for key in FIELDS:
    try:
      values[key] = decode_param(params.get(key))
    except Exception:
      values[key] = ""
  return values


def clear_bool_later(key: str, delay: float = 3.0) -> None:
  def clear() -> None:
    try:
      Params().put_bool(key, False)
    except Exception:
      pass
  timer = threading.Timer(delay, clear)
  timer.daemon = True
  timer.start()


class WebUIHandler(BaseHTTPRequestHandler):
  server_version = "OPKR-WebUI/1.0"

  def log_message(self, fmt: str, *args: Any) -> None:
    print("webui:", self.address_string(), fmt % args)

  def _json(self, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.send_header("Cache-Control", "no-store")
    self.send_header("X-Content-Type-Options", "nosniff")
    self.send_header("X-Frame-Options", "DENY")
    self.end_headers()
    self.wfile.write(body)

  def _reject_untrusted(self) -> bool:
    if is_allowed_client(self.client_address[0]):
      return False
    self._json(403, {"ok": False, "error": "로컬 네트워크 또는 Tailscale에서만 접속할 수 있습니다."})
    return True

  def _read_json(self) -> Dict[str, Any]:
    length = min(int(self.headers.get("Content-Length", "0")), 65536)
    if length <= 0:
      return {}
    return json.loads(self.rfile.read(length).decode("utf-8"))

  def do_GET(self) -> None:
    if self._reject_untrusted():
      return
    if self.path in ("/", "/index.html"):
      try:
        body = INDEX_FILE.read_bytes()
      except OSError:
        self._json(500, {"ok": False, "error": "WebUI 화면 파일을 읽을 수 없습니다."})
        return
      self.send_response(200)
      self.send_header("Content-Type", "text/html; charset=utf-8")
      self.send_header("Content-Length", str(len(body)))
      self.send_header("Cache-Control", "no-cache")
      self.send_header("X-Content-Type-Options", "nosniff")
      self.send_header("X-Frame-Options", "DENY")
      self.end_headers()
      self.wfile.write(body)
      return

    if self.path == "/api/settings":
      params = Params()
      editable, vehicle_state = modification_status(params)
      self._json(200, {
        "ok": True,
        "offroad": is_offroad(params),
        "editable": editable,
        "vehicleState": vehicle_state,
        "sections": SECTIONS,
        "values": current_values(params),
        "port": PORT,
      })
      return

    self._json(404, {"ok": False, "error": "찾을 수 없습니다."})

  def do_POST(self) -> None:
    if self._reject_untrusted():
      return
    if self.headers.get("X-WebUI-Request") != "1":
      self._json(403, {"ok": False, "error": "잘못된 요청입니다."})
      return
    try:
      data = self._read_json()
    except (ValueError, UnicodeDecodeError):
      self._json(400, {"ok": False, "error": "요청 형식이 올바르지 않습니다."})
      return

    params = Params()
    editable, _ = modification_status(params)
    if not editable:
      self._json(409, {"ok": False, "error": "속도 0 상태가 2초 이상 유지되고 OP가 꺼져 있어야 설정을 변경할 수 있습니다."})
      return

    if self.path == "/api/param":
      key = data.get("key")
      value = data.get("value")
      field = FIELDS.get(key) if isinstance(key, str) else None
      if not field or field["readOnly"]:
        self._json(400, {"ok": False, "error": "변경할 수 없는 설정입니다."})
        return
      if not isinstance(value, (str, int, float, bool)):
        self._json(400, {"ok": False, "error": "설정 값이 올바르지 않습니다."})
        return
      if field["type"] == "bool":
        normalized = "1" if value is True or str(value).lower() in ("1", "true") else "0"
      else:
        normalized = str(value)
      if len(normalized.encode("utf-8")) > 16384:
        self._json(413, {"ok": False, "error": "설정 값이 너무 깁니다."})
        return
      try:
        params.put(key, normalized)
      except Exception as error:
        self._json(500, {"ok": False, "error": f"설정을 저장하지 못했습니다: {error}"})
        return
      self._json(200, {"ok": True, "key": key, "value": normalized})
      return

    if self.path == "/api/action":
      action = data.get("action")
      try:
        if action == "refresh":
          params.put_bool("OnRoadRefresh", True)
          clear_bool_later("OnRoadRefresh")
        elif action == "reset_calibration":
          params.delete("CalibrationParams")
          params.delete("LiveParameters")
          params.put_bool("OnRoadRefresh", True)
          clear_bool_later("OnRoadRefresh")
        elif action == "reboot":
          params.put_bool("DoReboot", True)
        elif action == "shutdown":
          params.put_bool("DoShutdown", True)
        else:
          self._json(400, {"ok": False, "error": "지원하지 않는 작업입니다."})
          return
      except Exception as error:
        self._json(500, {"ok": False, "error": f"작업을 실행하지 못했습니다: {error}"})
        return
      self._json(200, {"ok": True, "action": action})
      return

    self._json(404, {"ok": False, "error": "찾을 수 없습니다."})


def run_server() -> None:
  server = ThreadingHTTPServer(("0.0.0.0", PORT), WebUIHandler)
  server.daemon_threads = True
  server.timeout = 1.0
  print(f"webui: listening on 0.0.0.0:{PORT}")
  try:
    while Params().get_bool("WebUIEnabled"):
      server.handle_request()
  finally:
    server.server_close()


def main() -> None:
  # process_config preimports modules in manager before forking. Start the
  # messaging thread here so it belongs to the actual WebUI child process.
  VEHICLE_ACTIVITY.start()
  while True:
    try:
      if Params().get_bool("WebUIEnabled"):
        run_server()
      else:
        time.sleep(1)
    except Exception as error:
      print("webui: restarting after error:", error)
      time.sleep(2)


if __name__ == "__main__":
  main()
