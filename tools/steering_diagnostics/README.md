# 읽기 전용 조향 기록 도구

기존 C2 메시지를 SSH로 받아 Mac의 새 폴더에 보관하는 수집기와, 완료된 파일을 해석하는 도구다. CAN 발행, Panda USB 직접 점유, Params 쓰기, daemon 시작, 재부팅, 제어기 실행은 하지 않는다.

수집용 프로세스는 CPU·네트워크를 사용한다. 수신만 한다는 사실이 실차 성능이나 안전성 보증을 뜻하지 않는다. 이 도구로 운전자 개입을 늦추거나 자동조향 한계를 시험하도록 권하지 않는다.

## 현재 형식: geometry-v2

- `capture_steering_geometry.py`: Mac에서 실행. SSH 원격 수신기를 표준입력으로 전달하고 새 `outputs/steering-geometry-*` 폴더에 저장한다.
- `steering_receive_geometry.py`: C2에서 이미 발행되는 CAN, 모델 경로, 차선·도로 경계 및 불확실성, 조향 상태, 회전·가속도 추정값, 보정값을 구독한다.
- 이미 실행 중인 전방 카메라의 RGB 화면을 목표 2 fps로 저장한다. 운전자 카메라·음성은 기록하지 않는다. native thumbnail도 별도로 보관한다.
- `interpret_geometry_capture.py`: 저장된 geometry-v2 측정만 읽는다. 시뮬레이션이나 제어기 실행을 하지 않으며 별도 파일도 쓰지 않는다.

수집하려면 승인된 C2의 SSH 대상을 `C2_SSH_TARGET` 환경변수에 지정한 뒤 저장소 루트에서 `python3 -B tools/steering_diagnostics/capture_steering_geometry.py 900`을 실행한다. 실행 즉시 수집이 시작되며 최대 900초로 제한된다. 호스트 주소·키·토큰은 소스에 넣지 않는다. SSH/Tailscale 추가 인증이 필요하면 실제 수신 전에 사용자가 승인해야 한다.

수집은 Ctrl+C로 종료한다. 로컬 프로세스는 이 실행에서 생성한 SSH 프로세스만 종료하고 파일을 닫는다. 기존 출력 폴더와 차량 파일은 삭제하거나 덮어쓰지 않는다. 잘린 마지막 전송 줄은 별도 파일로 보존될 수 있다.

완료된 폴더의 해석 예시:

```text
python3 -B tools/steering_diagnostics/interpret_geometry_capture.py <캡처폴더>
python3 -B tools/steering_diagnostics/interpret_geometry_capture.py <캡처폴더> 150 165 0.5
```

`status.json`의 `capture_inputs_present`는 필요한 입력이 최근에 도착했다는 뜻일 뿐, 차량의 자동조향이 안전하다거나 경계 추정이 정확하다는 허가가 아니다. `valid`, 시간 차이, 모델 신뢰도, 운전자 개입을 함께 해석해야 한다.

분석 요약의 구간 목록은 기록 공백 때문에 쪼개질 수 있다. 예를 들어 `op_active` 목록이 여러 구간이어도 실제 비활성 전환이 있었다는 뜻은 아니다. 실제 상태 변경은 저장된 Boolean 전이를 따로 읽는다.

## 시간·단위 제한

- CAN/제어/모델은 C2 메시지의 `source_ns`로 연결한다. 수신 지연과 차량의 제어 지연을 동일시하지 않는다.
- 현재 Python VisionIPC 바인딩은 RGB 프레임의 번호와 노출 시각을 제공하지 않는다. RGB JPEG는 수신 시각 기반 근접 장면이며 정밀 프레임 동기화 자료가 아니다.
- native thumbnail에는 frameId/timestampEof가 있지만 간격이 길다.
- MDPS 출력 보고값은 실제 Nm로 환산하지 않는다. `actuatorsOutput`은 host 명령이지 물리 출력 측정값이 아니다.
- CAN 송신 확인은 프레임이 송신됐다는 의미다. MDPS가 같은 크기의 실제 보조력을 냈다는 증거와 구분한다.
- 차량별 CAN 해석과 상한 384를 전제로 한 진단 코드다. 다른 차량의 범용 해석기로 사용하지 않는다.

## 두 형식의 요청 여력 선별 집계

`summarize_request_headroom.py`는 이전 형식과 geometry-v2의 저장 원본을 읽어 계산 요청·실제 송신·MDPS 상태를 연결한다. 캐시나 출력 파일을 만들지 않고 표준출력으로 요약한다. controller·MPC를 실행하거나 새 CAN 명령을 만들지 않는다.

```text
python3 -B tools/steering_diagnostics/summarize_request_headroom.py <이전캡처폴더> <geometry-v2캡처폴더>
```

이 도구의 기준값은 샘플 선별용이지 튜닝 설정이 아니다. 상한 아래인 표본을 찾았다고 안전하게 더 쓸 수 있는 토크가 증명되는 것은 아니다. 시각 차이·운전자 개입·오류·제한 적용을 함께 분리하며, [판독 방법과 한계](../../docs/steering_diagnostics/2026-09-13/final-record-review-ko.md)를 따라 해석한다. 이번 두 캡처와 같은 버스·CAN 신호 정의 및 동일한 기본/가변 상한을 전제로 한다.

## 이전 형식 보존

- `capture_steering.py`, `steering_receive.py`: geometry-v2 이전의 수집기.
- `analyze_recording.py`: 이전 형식의 요약 도구. 로컬 `decoded-recording.pickle` 캐시를 만들거나 읽는다. 신뢰하는 자체 캡처만 사용한다.
- `interpret_path_chain.py`: 9월 8일 이전 형식의 특정 시간 범위를 읽는 경로·토크 해석 도구. 기본 시간 범위가 해당 기록에 맞춰져 있다.

이전 분석기와 geometry-v2 입력 형식을 섞지 않는다. 원본 영상·주행 로그·전체 설정 스냅샷은 공개 저장소가 아닌 로컬에 보관한다.
