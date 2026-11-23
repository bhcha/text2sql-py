# 명령 표와 종료 코드

모든 명령은 엔진 스킬의 실행 스크립트로 부른다: `sh <엔진 스킬 폴더>/scripts/p3 <명령>`(아래에서 `p3 <명령>`). 작업 루트는 `T2S_ROOT`가 있으면 그것, 없으면 부른 폴더에서 위로 찾은 가장 가까운 `.text2sql/`다. 명령 출력의 `t2s <명령>`은 `p3 <명령>`으로 읽는다.

| 명령 | 하는 일 | 보기 |
|---|---|---|
| `fetch bird` | BIRD 자료·정답·임베딩 모델 받기와 해시 대조 | `p3 fetch bird` |
| `fetch model` | 임베딩 모델만 받기 | `p3 fetch model` |
| `fetch olist` | 스터디 Olist DB·평가셋 받기(gh)와 해시 대조, 정답은 `.text2sql/gold/olist/`로 | `p3 fetch olist` |
| `init` | 부른 폴더에 작업 폴더 `.text2sql/`를 만들고 엔진의 기본 설정·기준선을 복사(있는 파일은 덮어쓰지 않음). `--refresh`는 엔진의 기본 설정이 바뀌었는지 알리기만 함. 작업 루트 없이 받는다 | `p3 init` |
| `setup` | 전용 Python 환경만 만들고 그 자리를 알림. 작업 루트 없이 받는다 | `p3 setup` |
| `datasets` | 데이터셋 목록, 지금 바라보는 것, 준비 상태 | `p3 datasets` |
| `use` | 바라볼 데이터셋 바꾸기 | `p3 use shop` |
| `register` | SQLite 파일을 데이터셋으로 등록(고지 뒤 `--yes`) | `p3 register shop --sqlite <경로>/shop.sqlite --yes` |
| `prepare` | 값 색인·프로파일 만들기(준비된 것은 건너뜀) | `p3 prepare` |
| `ask` | 질문 하나 → SQL → 읽기 전용 실행 → 앞 10행 | `p3 ask --source financial --profile p3-ref "How many accounts are there?"` |
| `run` | 문항 묶음 실행(분리 실행, 끝나면 채점·보고까지). `--questions`는 공개 문항 꼴 JSON, 스터디 모델 입력 JSON, 한 줄에 질문 하나인 텍스트. `--evidence used`면 문항의 evidence를 준다(기본 not_used) | `p3 run --profile p3-ref --limit 20` |
| `status` | 가장 최근(또는 `--run-id`) run의 진행, 10줄 안 | `p3 status` |
| `stop` | 도는 run 멈추기 | `p3 stop` |
| `resume` | 멈춘 run 이어 돌기(채점 전, 같은 커밋일 때만) | `p3 resume` |
| `score` | 정답 설정의 채점기(BIRD 공식 채점기 또는 스터디 채점기)로 채점. BIRD는 예측 파일을 받아 채점도 한다(스터디는 종료 코드 2, 예측 파일 채점의 라벨은 `evidence 미확인`). evidence used run이면 run에 준 evidence 글이 정답 쪽 원본과 글자 그대로 같은지도 본다. `--meta-time-out`이 없으면 BIRD 30초, 스터디 10초 | `p3 score --run <id>` |
| `report` | 라벨, EX, 기준과 위치별 비교, 누설 검사 한 줄. evidence 방식이 다른 run·기준선과는 견주지 않는다 | `p3 report` |
| `leak-audit` | 프롬프트에 정답·evidence가 들어갔는지 검사(evidence used run이면 evidence는 세지 않고 빠진 위치를 센다) | `p3 leak-audit --run <id>` |
| `route-start` | 하네스(전체 P3)·단일 호출 두 run을 짝으로 만들어 분리 프로세스 둘로 함께 돌림(끝나면 각각 채점·보고, 단일 호출 run이 먼저 채점) | `p3 route-start --profile p3-claude` |
| `route-status` | 가장 최근(또는 `--pair`) 짝 두 run의 진행, 10줄 안 | `p3 route-status` |
| `route-judge` | 짝 두 run(또는 `--harness`·`--candidate`)을 위치별로 견주어 경로를 판정하고 하네스 run 폴더에 판정 파일을 씀(덮어쓰지 않음) | `p3 route-judge` |
| `route-apply` | 판정을 작업 루트의 `harness.toml` 프로필에 더할 글을 보임. `--yes`가 있을 때만 씀(종료 코드 3 승인 필요). 소표본·가짜 백엔드·미커밋 측정·판정 보류는 거절 | `p3 route-apply --yes` |

`ask`·`run`은 스스로 준비하지 않는다. 자산이 없거나 낡았으면 모델을 부르기 전에 종료 코드 5로 멈추므로 `prepare`를 먼저 돌린다. 소스가 하나인 데이터셋이면 `ask`에 `--source`를 붙이지 않는다. 모델 인자가 없으면 세션 기록에서 모델을 찾고, 못 찾으면 종료 코드 2로 멈춘다. 정답이 없는 데이터셋에서 `run`은 0으로 끝나고(생성 뒤 이어 부른 채점만 4), `score`·`leak-audit`은 4다.

모델 인자(`--profile`, `--backend`, `--model`, `--effort`, `--model-source`, `--effort-source`, `--session`)는 스킬이 넘기는 내부 계약이다. 손으로 칠 때는 `--profile p3-ref`(codex-cli·gpt-5.6-terra·medium), `--backend claude-code --model claude-opus-5-5`, 모델 호출 없는 시험용 `--backend fake`를 쓰면 된다.

종료 코드: 0 성공, 1 실패, 2 쓰임새 오류(소스·모델을 정해야 함), 3 승인 필요(`register`, `route-apply`), 4 정답 없음(채점 생략), 5 준비 필요(`prepare`를 먼저).

스킬이 부르는 차례는 각 스킬의 글에 적혀 있다. 엔진 스킬의 SKILL.md는 명령 묶음만 한 줄씩 적는다.
