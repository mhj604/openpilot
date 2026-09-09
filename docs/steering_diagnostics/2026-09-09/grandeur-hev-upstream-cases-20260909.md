# 그랜저 IG HEV: openpilot·당근파일럿·OPKR의 사용자 기록과 기존 구현

> 게시용 보관본. 작성 시점과 후속 정정을 구분한다. 최신 요약과 읽는 순서는 [기록 안내](README.md)를 참조한다. 원본 주행 데이터·화면·비공개 첨부는 로컬 보관이며 저장소에 포함하지 않는다.

조사일: 2026-09-09. 기존 코드·Params·차량·펌웨어 변경, 재부팅, 새 주행 시험, QA는 하지 않았다. 이 문서만 새로 작성했다.

## 결론

동일 차종의 기존 개발·사용 자료가 실제로 있다. 공식 openpilot에는 2019년식 IG 하이브리드 사용자가 제출한 서로 다른 주행 기록이 있고, 당근파일럿과 OPKR에도 초기형 IG HEV의 별도 차량 정의가 있다. 앞선 조사에서 순정 SCC·카메라 부재 조건이 명시된 장착 후기만 찾느라 이러한 자료를 충분히 활용하지 못했다.

다만 다음을 분리한다.

- 동일 차종의 주행 기록: 확인됨.
- 초기형 IG HEV용 차량 정의 및 조향 설정: 확인됨.
- OPKR에 SCC 없는 구성을 처리하는 코드: 확인됨.
- 위 세 사실을 합쳐 '비-SCC·비-MFC IG HEV + C2 + 동일 MDPS로 급커브 완주'라고 결론 내리기: 아직 근거 부족.
- 사용자가 목격한 다른 사례가 없다고 판단하기: 불가. 이번에 확보한 공개 자료의 범위가 제한됨.

## 1. 실제 사용자가 제출한 공식 openpilot 기록

### chanhojung — 2019년식 Azera/Grandeur Hybrid

- [PR #30548](https://github.com/commaai/openpilot/pull/30548), 2023-11-29 작성, 병합됨.
- 제목은 `Hyundai: add FW for 2019 AZERA_HEV_6TH_GEN`.
- 본문 템플릿의 `5TH_GEN` 표기는 제목·실제 변경 대상인 `AZERA_HEV_6TH_GEN`과 다르므로 그대로 차종 근거로 사용하지 않았다.
- 최초 제출 경로: `9e681cd13392bf93|2023-11-29--20-39-06`.
- 후속 경로: `9e681cd13392bf93|2023-11-30--13-58-33`, `9e681cd13392bf93|2023-11-30--20-29-02`.
- 작성자는 댓글에서 본인의 차가 Hyundai Azera/Grandeur라고 직접 설명했다.
- 함께 추가된 식별 정보:
  - EPS: `56310M9350`, `4IH8C101`.
  - 전방 카메라: `99211-G8000`.
  - SCC 레이더: `99110-M9000`.
- 따라서 같은 연식·차종의 직접적인 참고 기록이다. 하지만 카메라·SCC가 없는 사용자 구성과 동일하지 않다.
- 이 기록만으로 C2 사용, MDPS 교체 유무, 급커브 무개입 완주까지 확인되지는 않는다. 주행 원본을 새로 내려받아 분석하지 않았다.

### abraxas4 — 다른 2019년식 Azera HEV

- [PR #31684](https://github.com/commaai/openpilot/pull/31684), 2024-03-03 작성, 병합됨.
- 본문에 `Hyundai Azera HEV, 6th Generation, 2019` 명시.
- 경로: `3a0cde9552891b34/2024-03-03--10-33-30`.
- 추가 EPS 식별 정보: `56310M9350`, `4IH8C102`.
- 함께 추가된 카메라와 레이더 식별 정보가 있으며, 유지보수자도 댓글에서 카메라·레이더·EPS가 bus 0에서 조회된다고 설명했다.
- 첫 번째와 다른 계정·장치의 동일 차종 자료다. 역시 비-SCC/비-MFC 사례로 분류하지 않는다.

### 2020년식 지원의 출발점

- [PR #29946](https://github.com/commaai/openpilot/pull/29946): haram-KONA가 제출, 2020–2022 Azera Hybrid 지원 및 경로 기재.
- [PR #29984](https://github.com/commaai/openpilot/pull/29984): chanhojung이 이어서 제출, 병합됨.
- 경로: `66189dd8ec7b50e6|2023-09-20--07-02-12`.
- EPS `56310M9600 / 4IHSC100`, 전방 카메라와 SCC 레이더가 포함됨.
- 후기형을 포함하는 지원 이력이므로 초기형 2019와 사양·차량 기본값을 혼용하지 않는다.

## 2. 당근파일럿에는 초기형 IG HEV 별도 설정이 존재

읽은 저장소와 고정 기준:

- `ajouatom/apilot`, `c3-master`, `a02fb8313dee787e5a355e122613f2ce05e1f9b5` (2024-04-25).
- `ajouatom/openpilot`, `carrot-wip`, `2dac67cc4ad5486a4ceddf42f66208ee6763acbd` (2026-09-09). 작업 중인 개발 분기이며 C2 설치 추천이 아니다.

구 apilot:

- `GRANDEUR_IG_HEV = "HYUNDAI GRANDEUR IG HEV 2019"` 항목.
- 초기형과 FL을 별도로 다루며 초기형 wheelbase 2.845 m, steerRatio 16을 지정하는 코드가 있다.
- `override.yaml`의 `Manually checked` 구역에 IG HEV 2019의 `[2.8, 2.3, 0.1]` 항목이 있다.
- 파일의 필드 순서는 `LAT_ACCEL_FACTOR`, `MAX_LAT_ACCEL_MEASURED`, `FRICTION`이다. 이 주석·필드명만으로 특정 차주의 원본 측정 결과나 안전성이 증명됐다고 해석하지 않는다.

[구 apilot 차량 정의](https://github.com/ajouatom/apilot/blob/a02fb8313dee787e5a355e122613f2ce05e1f9b5/selfdrive/car/hyundai/values.py#L110)

[구 apilot 조향 기본값](https://github.com/ajouatom/apilot/blob/a02fb8313dee787e5a355e122613f2ce05e1f9b5/selfdrive/car/torque_data/override.yaml#L71)

조회 시점 carrot-wip:

- `HYUNDAI_GRANDEUR_IG_HEV` / `Hyundai Grandeur HEV 2018-19`가 별도 항목으로 존재.
- 별도로 존재하는 `HYUNDAI_AZERA_HEV_6TH_GEN`과 동일 항목으로 취급하지 않는다.
- 초기형 전용 항목: wheelbase 2.845 m, steerRatio 16, tireStiffnessFactor 0.7, HYBRID 및 LEGACY 플래그.
- 초기형 전용 torque_data 항목 `[2.3, 2.3, 0.1]`. AZERA_HEV 항목은 `[1.8, 1.8, 0.1]`.
- 한편 초기형 mass는 이 분기에서 1570으로 설정되어 있다. 코드에 값이 있다는 사실과 사용자 차량의 정확한 물리 사양이라는 판단은 구분한다.

[당근 초기형 IG HEV 항목](https://github.com/ajouatom/openpilot/blob/2dac67cc4ad5486a4ceddf42f66208ee6763acbd/opendbc_repo/opendbc/car/hyundai/values.py#L732)

[당근 초기형 IG HEV 조향 기본값](https://github.com/ajouatom/openpilot/blob/2dac67cc4ad5486a4ceddf42f66208ee6763acbd/opendbc_repo/opendbc/car/torque_data/override.toml#L96)

이 숫자들은 사용자의 WebUI 입력값에 그대로 대응하지 않는다. 코드 내 기본값과 해당 사용자가 실제 실행한 Params도 다를 수 있다. 이번에 값을 이식하거나 수정하지 않았다.

## 3. OPKR의 같은 차종 및 비-SCC 코드

기준: `openpilotkr/openpilot`, `OPKR`, `3ff6dc6081c2a986c75292eefa0952641ab66b6f` (2025-05-01).

- `GRANDEUR_HEV_IG = "HYUNDAI GRANDEUR HYBRID (IG)"`.
- 후기형 `GRANDEUR_HEV_FL_IG`와 분리.
- interface에서 SCC 메시지의 bus를 찾지 못하면 `sccBus = -1`, `radarOffCan = True`로 다룸.
- carstate에는 `no_radar` 분기가 있고, SCC 상태 대신 일반 크루즈 관련 신호를 읽는 코드가 있음.
- Panda safety에는 주석 `cruise control for car without SCC`와 RES/SET 버튼 처리 분기가 존재.
- 그러나 일부 경로는 특정 일반 크루즈 신호를 가정하고 다른 부분에서 카메라/SCC 메시지를 계속 참조한다. 따라서 이 코드 존재만으로 비-MFC IG HEV 전체 구성이 수정 없이 완성됐다고 주장하지 않는다.
- 저장된 IG HEV CAN fingerprint 표본은 SCC/LKAS에 해당하는 메시지 ID를 포함한다. 별도 무옵션 차주의 성공 주행 기록으로 세지 않는다.

[OPKR 차량 정의](https://github.com/openpilotkr/openpilot/blob/3ff6dc6081c2a986c75292eefa0952641ab66b6f/selfdrive/car/hyundai/values.py#L44)

[OPKR 비-SCC 판별](https://github.com/openpilotkr/openpilot/blob/3ff6dc6081c2a986c75292eefa0952641ab66b6f/selfdrive/car/hyundai/interface.py#L39)

[OPKR 일반 크루즈 처리](https://github.com/openpilotkr/openpilot/blob/3ff6dc6081c2a986c75292eefa0952641ab66b6f/selfdrive/car/hyundai/carstate.py#L211)

[OPKR 비-SCC safety 경로](https://github.com/openpilotkr/openpilot/blob/3ff6dc6081c2a986c75292eefa0952641ab66b6f/panda/board/safety/safety_hyundai_community.h#L99)

## 4. 급커브 해법을 해석할 때 중요한 차이

### 조향 명령 상한과 안전 제한이 같지 않다

- 사용자 확정 기록의 송신 상한은 384였다.
- 조사한 당근/apilot 분기에는 기본 조향 명령 상한 409인 경로가 있다. 최신 분기는 `CustomSteerMax`로 실행 중 덮어쓸 수도 있다.
- Panda 안전 제한 역시 분기별로 다르다. 그러므로 다른 분기의 성공 경험을 동일한 384·동일한 운전자 개입 보호 조건의 결과라고 가정하면 안 된다.
- 409는 384보다 명령 숫자가 약 6.5% 크다는 뜻일 뿐, 실제 조향 보조력이 그만큼 증가하거나 해당 급커브를 완주한다는 뜻이 아니다.
- 이전 사용자 작업에서는 host와 Panda 상한이 어긋나는 문제가 있었으므로, 이번 공개 코드 발견을 '이미 안전하게 검증된 409 해법'으로 재포장하지 않는다. 제한 상향이나 보호 약화를 권고하지 않는다.

[당근 명령 상한 코드](https://github.com/ajouatom/openpilot/blob/2dac67cc4ad5486a4ceddf42f66208ee6763acbd/opendbc_repo/opendbc/car/hyundai/values.py#L38)

[당근 실행 중 설정 반영 경로](https://github.com/ajouatom/openpilot/blob/2dac67cc4ad5486a4ceddf42f66208ee6763acbd/opendbc_repo/opendbc/car/hyundai/carcontroller.py#L237)

### 같은 차종의 튜닝 제안도 채택 여부를 구분해야 한다

- [PR #30096](https://github.com/commaai/openpilot/pull/30096)는 chanhojung이 Azera/Hybrid의 토크 환산 관련 값을 `[1.8, 1.8, 0.1]`에서 `[2.5, 2.5, 0.1]`로 바꾸자고 제안한 내용.
- 유지보수자가 주행 근거를 요구했고, 해당 PR은 병합되지 않았다.
- 같은 플랫폼이라는 작성자의 추정이나 숫자 변경 제안을 검증된 급커브 해결책으로 인용하지 않는다.

### EPS 부품번호의 해석

- 실제 2019 사용자 기록의 EPS 식별 문자열에는 `56310-M9350`이 있다.
- 앞서 찾은 컨트롤러 후보 `56340-M9000/M9050`과는 부품 구분이 다르다.
- 이 자료만으로 M9350이 더 강한 조향을 제공하거나 사용자의 교체 정답이라고 결론 내리지 않는다. 컨트롤러와 컬럼 대응 관계도 확정하지 않았다.

## 앞으로 비교할 자료의 우선순위

같은 차종의 기존 구현을 비교할 근거는 충분하다. 후기형을 합친 일반 Azera 항목만 볼 것이 아니라 당근의 초기형 `GRANDEUR_IG_HEV`와 OPKR의 `GRANDEUR_HEV_IG`를 구분해 참고해야 한다.

그 비교에서 중요한 것은 명령 생성·전송과 차량 옵션·EPS 버전·실행 설정의 차이다. 하지만 이번 연구 결과만으로 MDPS 교체가 필요하다고 확정하거나, 반대로 특정 브랜치를 넣으면 급커브가 해결된다고 보장할 수 없다. 반응 지연을 다시 주된 시험 대상으로 돌리지 않는다.

## 조사 방법

Search 스킬로 세 저장소 계열을 검색했다. Exa 검색 요청의 결과 수 합계는 15건이며 중복·무관 결과를 포함한다. 핵심 근거는 GitHub 공개 PR 본문·댓글·변경 내역과 고정 커밋의 소스다. 공개 API로 기존 기록을 읽었으며 제안·댓글·문의·PR을 게시하지 않았다.

보조적으로 YouTube 공개 검색도 읽었지만, 새로 발견한 동일 조건의 독립 사용자 영상은 없어 이전 시공 영상을 추가 성공 사례로 세지 않았다. 코드에 존재하는 기능, 사용자 제출 주행 기록, 급커브 성능 입증은 서로 다른 증거 수준으로 정리했다.
