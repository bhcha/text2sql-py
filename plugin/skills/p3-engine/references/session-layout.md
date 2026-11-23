# 작업 폴더에 무엇이 놓이는가

작업 루트는 사용자가 일하는 폴더의 `.text2sql/`이다(`p3 init`이 만든다). 안의 `.gitignore`가 `*`이라 사용자 프로젝트의 git에는 들어가지 않는다. 엔진 스킬 폴더와 플러그인 폴더에는 아무것도 생기지 않는다. 실행 기록은 세션 폴더 `.text2sql/sessions/<세션 이름>/`에 놓인다(`init`이 작업 폴더의 `harness.toml`에서 `[paths] runs`를 `sessions`로 바꿔 쓴다). 세션 하나는 실행 기록 폴더 하나다.

## 세션 이름

- 새 이름: `YYYY-MM-DD-<데이터셋>-<종류>-<16진 6자>`. 보기: `2026-11-23-bird-minidev-set-665ae5`, `2026-11-23-olist-ask-a6addc`.
- 종류: `set`(`run`·`route-start`), `ask`(질문 하나), 지표 길의 세션 `metric`·`explore`·`check`·`diagnose`·`define`(`session open`). 지표 길 세션의 데이터셋 칸은 지금 바라보는 데이터셋이고 없으면 `none`이다.
- 경로 짝(`route-start`)의 두 run은 짝 이름 끝에 `-harness`, `-single`이 붙는다.
- `run --run-id <이름>`으로 이름을 주면 그 이름을 그대로 쓴다.
- 옛 이름 `YYYYMMDD-<데이터셋>-<16진 6자>`는 읽기만 한다(새로 만들지 않는다).
- `.text2sql/sessions/index.jsonl`: 세션을 시작할 때마다 한 줄(세션 이름, 종류, 데이터셋, 시작 시각, 대화 세션 ID가 환경에 있으면 그 값). 덧붙이기만 한다.
- `p3 sessions`가 색인과 세션 폴더의 작은 파일에서 최근 세션 20개를 한 줄씩 낸다. 세션을 지우거나 이름을 바꾸는 명령은 없다.

## 놓이는 것


- `.text2sql/format`, `.text2sql/init.json`: 작업 폴더의 꼴 번호와, 만들 때 복사한 기본 설정·기준선의 sha256.
- `.text2sql/harness.toml`: 설정. `init`이 엔진의 기본 설정을 복사한 것이고, `route-apply`가 여기에 쓴다. 엔진의 기본 설정이 바뀌었는지는 `p3 init --refresh`가 알린다(쓰지 않는다).
- `.text2sql/baselines/`: 기준선(덮어쓰지 않는 기록).
- `.text2sql/sessions/<세션 이름>/`(종류 `set`): `run.json`(시작 조건: 모델, 커밋, 판, DB 지문, evidence 방식. 플러그인 설치본이면 `engine` 칸에 엔진 판·체크섬·깔린 코드가 표식과 맞는지), evidence used run이면 `hints.json`(위치별로 준 evidence 글), `control.json`(진행 상태), `run.log`, `items/<위치>/`(`record.json`, `trace.json`, 호출마다 `calls/<k>/prompt.txt`·`response.txt`·`llm-meta.json`·`cli-events.jsonl`), `records.jsonl`, `predictions.json`, `manifest.json`, 채점 뒤 `score.json`·`ex.jsonl`·`evaluator-stdout.txt`·`leak-audit.json`, 보고 뒤 `report.json`·`report.md`. 큰 파일(`records.jsonl`, `items/`)은 통째로 읽지 않는다.
- `run.json`의 `route` 칸: 적용 경로(`applied`: single·harness·partial), 프로필이 요청한 경로(`requested`), 정한 곳(`source`), 까닭(`reason`), 쓴 근거 사본(`basis`), 경고(`warnings`), partial의 덮어쓴 칸(`overrides`), 짝(`pair`), 모델 이름으로 맞춘 때의 프로필 이름들(`matches`), 추론 단계를 기본값으로 본 때 그 값(`assumed_effort`). 짝 판정 파일은 하네스 run 폴더의 `route-judgment-<후보 run>.json`이다.
- `.text2sql/sessions/<세션 이름>/`(종류 `ask`): `ask` 한 번의 `trace.json`·`ask.json`·`calls/`.
- `.text2sql/sessions/<세션 이름>/`(지표 길의 종류): `session.json`(질문, 데이터셋, `session note`로 적은 칸, 시작·끝 시각, 누설 검사 대상 아님 표시), text2sql-plugin의 스킬이 두는 `t2s-log.jsonl`(명령마다 한 줄, 결과 행 없음)과 `report.md`. 지표 길의 세션은 프롬프트 파일이 없어 누설 검사 대상이 아니다.
- 옛 run 폴더: 설정의 `[paths] legacy_runs`(폴더 경로 목록)에 적은 자리의 run은 `report --against`, `route-judge`, `status --run-id`가 세션 폴더에서 못 찾을 때 읽기만 한다. 그 폴더에는 쓰지 않는다(`route-judge`는 하네스 run이 옛 폴더에 있으면 판정 파일을 쓰지 않고, `resume`은 거절한다).
- `.text2sql/data/`: 받은 자료, 임베딩 모델, 자산(`data/assets/<데이터셋>/<소스>/`의 값 색인·프로파일·manifest), 공개 evidence 파일(`data/bird-minidev/hints.public.json`). 공개 evidence 파일은 evidence used run을 시작하는 프로세스만 연다. 받은 zip(`data/*/download/`)의 내용을 출력하지 않는다.
- `.text2sql/gold/`: 정답이 든 파일과 정답 설정, 채점 중간 파일(`gold/_work/<세션 이름>/`). 채점(`score`·`report`·`leak-audit`)만 읽는다. 생성 쪽(`ask`·`run`·`prepare`)은 읽기 감시를 걸고 돌아 이 폴더를 열지 못한다. 스킬은 이 폴더의 파일을 열거나 출력하지 않는다.
- `.text2sql/var/state.json`: 지금 바라보는 데이터셋과 등록 기록.
