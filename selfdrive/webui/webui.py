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
    field = {
      "key": key,
      "title": translated(title, translations) if title else humanize_key(key),
      "description": translated(description, translations) if description else "",
      "type": field_type(key, default, bool_keys),
      "default": default,
      "readOnly": key not in editable_keys,
      "source": class_name,
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
