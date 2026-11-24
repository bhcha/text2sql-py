# text2sql-py

P3 text-to-SQL 파이프라인(11단계)을 Python으로 다시 만든 하네스다. SQLite DB에 자연어로 묻고, 문항 묶음을 한꺼번에 돌리고, 정답이 있으면 잰다(BIRD Mini-Dev는 공식 채점기, Olist 스터디 평가셋 v0.3은 스터디 채점 규칙). 규칙은 `AGENTS.md`, 출처는 `docs/provenance.md`에 있다.

## 먼저 알 것

- 밖으로 나가는 것: 질문, CREATE 원문과 예시 3행, 질문과 비슷한 값, 열 프로파일(범주형 열은 값 전부), 실행 오류 글이 모델 제공자 서버(OpenAI 또는 Anthropic)로 나간다. evidence used run에서는 문항의 evidence 글도, semantic used일 때는 받아들인 지표 정의 글(승인 지표의 이름·설명·동의어·쓰임 안내·기준 SQL)도 나간다. 정답 SQL은 나가지 않는다. 민감한 DB에 쓰지 않는다. 엔진은 Graph MCP 서버로 아무것도 보내지 않는다.
- evidence: 기본은 퓨어 조건(evidence not_used)이다. 문항의 evidence(BIRD 공식 evidence, Olist 문항 조건)를 모델에 주는 evidence used는 `/runset`에 "evidence 줘서", "문항 조건 넣어서"처럼 말했을 때만 도는 별도 조건이다(아래 "evidence used로 돌리기").
- semantic: 받아들인 승인 지표 정의를 모델에 주는 semantic used는 `/text2sql`·`/runset`에 "지표 정의 넣어서"처럼 말했을 때만 도는 또 하나의 별도 조건이다(아래 "semantic used로 돌리기"). 조건은 셋이다: 퓨어(evidence not_used이고 semantic not_used), evidence used, semantic used. evidence used와 semantic used는 함께 쓰지 않는다.
- DB는 읽기 전용으로만 연다(`mode=ro&immutable=1`, 쓰기 거절, 실행 전후 sha256 비교). 다른 프로그램이 그 DB에 쓰고 있지 않아야 한다.
- 모델 호출은 이 기기의 `codex` 또는 `claude` 명령으로 한다(구독 사용량이 든다). 500문항은 약 1,000~1,500회 호출, 30~70분이다.

## 필요한 것

- macOS, uv, git, curl. Python 3.13.7은 uv가 받는다. 첫 `uv run`이 실행 의존(numpy·onnxruntime·tokenizers)과 채점 의존(func-timeout·psycopg2-binary·PyMySQL)을 깐다.
- `codex` 또는 `claude` 명령(로그인된 상태).
- 디스크 약 3GB(BIRD zip 0.8GB, 푼 DB 1.4GB, 임베딩 모델 0.4GB).
- `/getolist`를 쓸 때만: GitHub CLI(gh) 로그인과 스터디 저장소(비공개) 접근 권한.

## 스킬로 쓰기 (Claude Code는 /이름, Codex는 $이름)

스킬은 text2sql-plugin 플러그인으로 깔아 쓴다. 저장소 안에 따로 둔 스킬 폴더(`.claude/skills/`, `.agents/skills/`)는 없앴고, 스킬 글의 원본은 `plugin/skills/`에 있다. 배포된 플러그인에는 아직 이 스킬들이 실려 있지 않으므로, 지금은 아래의 로컬 경로 설치를 쓴다. 이 저장소에서 만든 판을 로컬 경로로 깔 때는, 커밋한 깨끗한 작업 트리에서 `uv run python scripts/build_engine_skills.py`로 엔진 스킬 폴더를 만들고(결과는 `dist/engine-skills/skills/`), 그 안의 스킬 여덟 폴더를 text2sql-plugin 사본의 `skills/`에 넣은 뒤, Claude Code는 쓸 프로젝트에서 `claude plugin marketplace add --scope local <사본 경로>` → `claude plugin install --scope local text2sql@text2sql-plugin`, Codex는 `codex plugin marketplace add <사본 경로>` → `codex plugin add text2sql@text2sql-plugin`으로 깐다. 플러그인으로 깔면 Claude Code에서는 `/text2sql:이름`으로 부르고(Codex는 `$이름`), 명령은 사용자 작업 폴더의 `.text2sql/` 아래에서 돈다(첫 명령에서 `init`을 물어본다).

| 차례 | 스킬 | 하는 일 | 걸리는 시간 |
|---|---|---|---|
| 1 | `/getbird` | BIRD Mini-Dev 자료와 임베딩 모델을 받고 해시를 대조, 정답은 `gold/`로, 공개 evidence 파일을 만듦 | 받기 1.2GB |
| 2 | `/connect` | 데이터셋 목록·준비·전환. `/connect bird`, `/connect olist`(고지 뒤 승인), `/connect <SQLite 경로>`(고지 뒤 승인), `/connect <이름>` | BIRD 준비 약 2분 |
| 3 | `/text2sql <질문>` | 지금 데이터셋에 질문 하나. BIRD면 DB 이름을 함께 말한다 | 수 초~수십 초 |
| 4 | `/runset` | 지금 데이터셋 전체(또는 "앞 N문항만")를 돌리고, 정답이 있으면 채점·기준 비교. Olist면 평가셋 49문항을 돌리고 스터디 채점 규칙으로 잰다 | 500문항 30~70분 |
| 따로 | `/getolist` | 스터디 Olist DB와 평가셋 v0.3(49문항)을 gh로 받고 해시를 대조, 정답은 `gold/olist/`로, 정답 설정 `gold/olist/gold.toml`을 만듦 | 받기 약 70MB |
| 따로 | `/getmetrics` | Graph MCP의 활성 지표 모델에서 승인 지표 정의를 text2sql-plugin의 `t2s export`로 받아 지금 데이터셋(SQLite 하나)에 맞는지 검사하고, 밖으로 나가는 것을 알리고 승인을 받은 뒤 받아들임. 받아들인 정의는 사용자가 "지표 정의 넣어서"라고 말할 때만 쓰인다 | 수 초 |
| 따로 | `/newmodel` | 새 모델(백엔드·모델·추론 단계)을 하네스(전체 P3)와 단일 호출 두 run으로 함께 돌리고, 위치별로 견주어 그 모델의 경로(harness·single)를 판정한 뒤 기록할 수 있는 판정이면 바로 `harness.toml` 프로필에 경로와 근거를 더하고 더한 줄 전체를 보임 | 500문항 두 run을 함께(걸리는 시간 미측정) |

- `/getbird` 뒤 바로 `/runset`을 불러도 된다(스킬이 `prepare`를 먼저 돌린다). 기본 데이터셋은 없다. `/text2sql`·`/runset` 전에 `/connect bird`, `/connect olist`, `/connect <경로>` 중 하나로 데이터셋을 정해야 한다. `bird`는 데이터셋 `bird-minidev`를 가리키는 말이다.
- `/getolist`는 받기만 한다(정답 설정 `gold/olist/gold.toml`도 이때 만든다). Olist DB를 쓰려면 `/connect olist`로 등록한다(고지 뒤 승인). 그 뒤 `/runset`이 평가셋 49문항을 돌리고 채점한다(아래 "Olist 평가셋 채점"). Olist 자료는 CC BY-NC-SA 4.0이고 스터디 용도로만 쓴다.
- BIRD의 DB 이름 11개: financial, card_games, california_schools, codebase_community, debit_card_specializing, european_football_2, formula_1, student_club, superhero, thrombosis_prediction, toxicology(`uv run t2s datasets`로도 본다).

스킬이 부르는 명령(스킬을 쓰지 않을 때 같은 일을 하는 차례):

| 스킬 | 명령 |
|---|---|
| `/getbird` | `fetch bird` |
| `/getolist` | `fetch olist` |
| `/connect bird` | `prepare bird-minidev` → `use bird-minidev` |
| `/connect olist` | `register olist --sqlite data/olist/olist.db`(고지) → 승인 뒤 `--yes` → `prepare olist` → `use olist`. 이미 등록돼 있으면 `use olist` |
| `/connect <경로>` | `register <이름> --sqlite <경로>`(고지) → 승인 뒤 `--yes` → `prepare <이름>` → `use <이름>`. 이름은 말한 것, 없으면 파일 이름에서 만든다. 끝나면(Olist도) 플러그인의 `t2s export`와 `semantic-check <이름>`으로 맞는 활성 지표 모델이 있는지 알리기만 한다 |
| `/getmetrics` | `datasets` → 플러그인의 `t2s export --out .text2sql/var/semantic-inbox/<이름>.json` → `semantic-import <이름>`(고지) → 승인 뒤 `--yes`(거절이나 실패면 `semantic-remove <이름> --inbox`) |
| `/text2sql <질문>` | `ask [--source <DB>] <모델 인자> "<질문>"`. 종료 코드 5면 `prepare` 뒤 한 번 더. `--semantic used`는 사용자가 지표 정의를 넣으라고 말했을 때만 |
| `/runset` | `status`로 할 일을 고른 뒤 `prepare` → `run <모델 인자> [--limit N] [--questions <파일>] [--evidence used]` → (물을 때) `status` → `report`. 지금 데이터셋이 olist면 늘 `--questions data/olist/model_inputs.v0.3.json`. `--evidence used`는 사용자가 evidence(문항 조건)를 주라고 말했을 때만, `--semantic used`는 지표 정의를 넣으라고 말했을 때만 |
| `/newmodel` | `route-status`로 할 일을 고른 뒤 `prepare` → `route-start <모델 인자> [--limit N] [--questions <파일>] [--evidence used]` → (물을 때) `route-status` → `route-judge` → `route-apply --yes`(기록할 수 있는 판정이면 묻지 않고 씀) |
- `/runset`은 시작하고 바로 돌아온다. "진행 어때"로 상태를, 끝난 뒤 "보고"로 결과를 본다.
- 모델은 지금 세션의 모델이 기본이다. "terra medium으로", "opus로"처럼 말하면 그 모델을 쓴다. 모델을 알 수 없으면 돌리지 않고 묻는다.

## 명령으로 쓰기

모든 명령은 저장소 루트(`harness.toml`이 있는 폴더)에서 `uv run t2s <명령>`으로 돌린다. 다른 루트를 쓰려면 `T2S_ROOT`를 준다.

| 명령 | 하는 일 | 보기 |
|---|---|---|
| `fetch bird` | BIRD 자료·정답·임베딩 모델 받기와 해시 대조 | `uv run t2s fetch bird` |
| `fetch model` | 임베딩 모델만 받기 | `uv run t2s fetch model` |
| `fetch olist` | 스터디 Olist DB·평가셋 받기(gh)와 해시 대조, 정답은 `gold/olist/`로 | `uv run t2s fetch olist` |
| `datasets` | 데이터셋 목록, 지금 바라보는 것, 준비 상태 | `uv run t2s datasets` |
| `use` | 바라볼 데이터셋 바꾸기 | `uv run t2s use shop` |
| `register` | SQLite 파일을 데이터셋으로 등록(고지 뒤 `--yes`) | `uv run t2s register shop --sqlite ~/db/shop.sqlite --yes` |
| `prepare` | 값 색인·프로파일 만들기(준비된 것은 건너뜀) | `uv run t2s prepare` |
| `semantic-check` | 받기 자리(`var/semantic-inbox/<이름>.json`)의 지표 정의를 그 데이터셋 DB와 대조해 한 줄로 알리기만 함. 받아들이지 않고 받기 자리의 파일을 지움 | `uv run t2s semantic-check olist` |
| `semantic-import` | 받기 자리의 정의를 꼴·승인·크기·테이블로 검사하고 고지(종료 코드 3), `--yes`면 `data/<이름>/semantic.approved.json`으로 옮겨 sha256을 `var/state.json`에 고정 | `uv run t2s semantic-import olist --yes` |
| `semantic-remove` | 받아들인 정의와 고정 기록을 지움. `--inbox`면 받기 자리의 파일만 | `uv run t2s semantic-remove olist` |
| `ask` | 질문 하나 → SQL → 읽기 전용 실행 → 앞 10행 | `uv run t2s ask --source financial --profile p3-ref "How many accounts are there?"` |
| `run` | 문항 묶음 실행(분리 실행, 끝나면 채점·보고까지). `--questions`는 공개 문항 꼴 JSON, 스터디 모델 입력 JSON, 한 줄에 질문 하나인 텍스트. `--evidence used`면 문항의 evidence를 준다(기본 not_used). `--semantic used`면 받아들인 지표 정의 글을 준다(기본 not_used, `ask`에도 있다) | `uv run t2s run --profile p3-ref --limit 20` |
| `status` | 가장 최근(또는 `--run-id`) run의 진행, 10줄 안 | `uv run t2s status` |
| `stop` | 도는 run 멈추기 | `uv run t2s stop` |
| `resume` | 멈춘 run 이어 돌기(채점 전, 같은 커밋일 때만) | `uv run t2s resume` |
| `score` | 정답 설정의 채점기(BIRD 공식 채점기 또는 스터디 채점기)로 채점. BIRD는 예측 파일을 받아 채점도 한다(스터디는 종료 코드 2, 예측 파일 채점의 라벨은 `evidence 미확인`). evidence used run이면 run에 준 evidence 글이 정답 쪽 원본과 글자 그대로 같은지도 본다. `--meta-time-out`이 없으면 BIRD 30초, 스터디 10초 | `uv run t2s score --run <id>` |
| `report` | 라벨, EX, 기준과 위치별 비교, 누설 검사 한 줄. evidence 방식이 다른 run·기준선과는 견주지 않는다 | `uv run t2s report` |
| `leak-audit` | 프롬프트에 정답·evidence가 들어갔는지 검사(evidence used run이면 evidence는 세지 않고 빠진 위치를 센다) | `uv run t2s leak-audit --run <id>` |
| `route-start` | 하네스(전체 P3)·단일 호출 두 run을 짝으로 만들어 분리 프로세스 둘로 함께 돌림(끝나면 각각 채점·보고, 단일 호출 run이 먼저 채점) | `uv run t2s route-start --profile p3-claude` |
| `route-status` | 가장 최근(또는 `--pair`) 짝 두 run의 진행, 10줄 안 | `uv run t2s route-status` |
| `route-judge` | 짝 두 run(또는 `--harness`·`--candidate`)을 위치별로 견주어 경로를 판정하고 하네스 run 폴더에 판정 파일을 씀(덮어쓰지 않음) | `uv run t2s route-judge` |
| `route-apply` | 판정을 `harness.toml` 프로필에 더할 글을 보임. `--yes`가 있을 때만 씀(종료 코드 3 승인 필요). 소표본·가짜 백엔드·미커밋 측정·판정 보류는 거절 | `uv run t2s route-apply --yes` |

`ask`·`run`은 스스로 준비하지 않는다. 자산이 없거나 낡았으면 모델을 부르기 전에 종료 코드 5로 멈추므로 `prepare`를 먼저 돌린다. 소스가 하나인 데이터셋이면 `ask`에 `--source`를 붙이지 않는다. 모델 인자가 없으면 세션 기록에서 모델을 찾고, 못 찾으면 종료 코드 2로 멈춘다. 정답이 없는 데이터셋에서 `run`은 0으로 끝나고(생성 뒤 이어 부른 채점만 4), `score`·`leak-audit`은 4다.

모델 인자(`--profile`, `--backend`, `--model`, `--effort`, `--model-source`, `--effort-source`, `--session`)는 스킬이 넘기는 내부 계약이다. 손으로 칠 때는 `--profile p3-ref`(codex-cli·gpt-5.6-terra·medium), `--backend claude-code --model claude-opus-5-5`, 모델 호출 없는 시험용 `--backend fake`를 쓰면 된다.

종료 코드: 0 성공, 1 실패, 2 쓰임새 오류(소스·모델을 정해야 함), 3 승인 필요(`register`, `route-apply`, `semantic-import`), 4 정답 없음(채점 생략), 5 준비 필요(`prepare`를 먼저).

## 자기 DB에 묻기

1. `/connect <SQLite 경로>`. 밖으로 나가는 것을 알리고 승인을 받은 뒤 등록 → 준비 → 전환한다.
2. `/text2sql <질문>`. 소스가 하나라 DB 이름은 말하지 않아도 된다.
3. 문항 묶음을 돌리려면 질문이 한 줄에 하나씩 든 텍스트 파일을 만들고 `/runset`에 그 경로를 알려 준다(명령으로는 `uv run t2s run --questions <파일>`). 빈 줄과 `#`로 시작하는 줄은 건너뛴다.
4. 정답이 없으므로 채점은 없다. `report`는 "정답 없음: 채점 생략"과 실행 요약(상태 수, 실행 상태 수, 고치기 수)을 내고, SQL과 실행 상태는 `runs/<run_id>/records.jsonl`에 남는다.

## 결과가 놓이는 곳

모두 git 밖이다.

- `runs/<run_id>/`: `run.json`(시작 조건: 모델, 커밋, 판, DB 지문, evidence 방식), evidence used run이면 `hints.json`(위치별로 준 evidence 글), `control.json`(진행 상태), `run.log`, `items/<위치>/`(`record.json`, `trace.json`, 호출마다 `calls/<k>/prompt.txt`·`response.txt`·`llm-meta.json`·`cli-events.jsonl`), `records.jsonl`, `predictions.json`, `manifest.json`, 채점 뒤 `score.json`·`ex.jsonl`·`evaluator-stdout.txt`·`leak-audit.json`, 보고 뒤 `report.json`·`report.md`.
- `run.json`의 `route` 칸: 적용 경로(`applied`: single·harness·partial), 프로필이 요청한 경로(`requested`), 정한 곳(`source`: default·profile·profile-match·forced), 까닭(`reason`: session·alias·no_profile·no_effort·no_route·no_basis·basis_mismatch·ambiguous·measured·forced), 쓴 근거 사본(`basis`), 경고(`warnings`), partial의 덮어쓴 칸(`overrides`), 짝(`pair`: route-start로 만든 run의 짝 이름·역할·상대 run), 모델 이름으로 맞춘 때 근거가 맞은 프로필 이름들(`matches`), 추론 단계를 기본값으로 보고 맞춘 때 그 값(`assumed_effort`). `ask.json`의 `route` 칸도 같은 꼴이다. 칸이 없는 옛 run은 harness로 읽는다. `pipeline` 칸은 실제 적용한 설정이다. 짝 판정 파일은 하네스 run 폴더의 `route-judgment-<후보 run>.json`이다.
- `runs/ask/<시각>-<16진>/`: `ask` 한 번의 `trace.json`·`ask.json`(같은 `route` 칸)·`calls/`.
- `data/`: 받은 자료, 임베딩 모델, 자산(`data/assets/<데이터셋>/<소스>/`의 값 색인·프로파일·manifest), 공개 evidence 파일(`data/bird-minidev/hints.public.json`, 위치별 question_id와 evidence 글만, sha256 고정). 공개 evidence 파일은 evidence used run을 시작하는 프로세스만 연다(그 밖에는 읽기 감시가 막는다).
- `gold/`: 정답이 든 파일(정답 SQL, 기대 결과, 채점 메모, 공식 문제 파일 원본)과 정답 설정, 채점 중간 파일(`gold/_work/<run_id>/`, 스터디 채점의 문항별 이유는 `detail.jsonl`). 채점(`score`·`report`·`leak-audit`)만 읽는다. 생성 쪽(`ask`·`run`)은 읽기 감시를 걸고 돌아 이 폴더를 열지 못한다.
- `var/state.json`: 지금 바라보는 데이터셋과 등록 기록, 받아들인 지표 정의의 고정 기록(`semantic` 칸: 경로, sha256, 크기, source 칸, 지표 수, 글자 수, 규칙 판, DB 지문, 받아들인 때).
- 지표 정의: 받기 자리 `var/semantic-inbox/<이름>.json`, 받아들인 파일 `data/<이름>/semantic.approved.json`(커밋하지 않는다). 둘 다 읽기 감시가 막고, 정의를 여는 프로세스는 `semantic-check`, `semantic-import`, semantic used인 `ask`, semantic used run을 시작하는 프로세스뿐이다. semantic used로 돈 `ask`·run 폴더에는 한 줄 글의 사본 `semantic.json`이 생기고, 분리된 프로세스와 `resume`은 이것만 읽는다(`run.json`의 sha256과 대조).

## 점수를 읽는 법

- 모든 점수에 라벨 전체가 붙는다. 예: `BIRD Mini-Dev / SQLite / cc48ba16 / 500문제 / EX / evidence not_used / 로컬 측정 / gpt-5.6-terra·추론 medium·codex-cli / 1회 실행 / 구현 Python <커밋>`. evidence used run이면 `evidence used`, 예측 파일 채점이면 `evidence 미확인`이 그 자리에 들어간다. `cc48ba16`은 BIRD zip의 sha256 앞 8자다. 미커밋 코드면 `/ (미커밋 코드)`, 앞 N문항만이면 `/ 소표본·점수 아님`, 가짜 백엔드면 `/ 가짜 백엔드·점수 아님`이 붙는다. 라벨을 줄여 쓰지 않는다.
- 기준(TS 기준선 `baselines/ts-pure-fix.json`, EX 263/500)과 위치별로 견주어 새로 맞음·새로 틀림을 함께 적는다. 차이가 13 이하이거나 부호 검정 p가 0.05 이상이면 "차이 없음(변동 범위)"이다. 같은 코드도 run마다 수십 위치가 갈리므로 1회 실행으로 낫다·못하다를 말하지 않는다.
- 참조 조건은 codex-cli·gpt-5.6-terra·medium이다. 다른 모델이면 "재현 비교 아님", 백엔드가 다르면 우열을 말하지 않는다.
- evidence 방식이 다른 점수끼리는 견주지 않는다(새로 맞음·새로 틀림도 내지 않는다). TS 기준선은 evidence not_used라 evidence used run의 `report`는 "비교 대상 없음(기준은 evidence not_used, 이번은 used라 견주지 않는다)"을 낸다. `--against`로 방식이 다른 run을 주면 "evidence 조건이 달라 견주지 않는다(…)"를 내고 종료 코드 1이다. 방식을 모르는 쪽(ex.jsonl, 칸 없는 옛 run)은 not_used로 본다. used 점수는 퓨어 조건 점수라고 부르지 않는다.
- 원 논문·리더보드 점수와 견주지 않는다(측정 조건이 다르다).
- semantic used run의 라벨에는 `evidence <…>` 바로 뒤에 `/ semantic used(<받아들인 정의 파일의 sha256 앞 8자>)`가 붙는다. 예: `Olist 스터디 평가셋 v0.3 / SQLite / 131eaf49 / 49문제 / EX(스터디 결과 비교) / evidence not_used / semantic used(3fa1c0de) / 로컬 측정 / gpt-5.6-terra·추론 medium·codex-cli / 1회 실행 / 구현 Python <커밋>`. 이 칸이 없는 라벨은 semantic not_used다. 시험용 정의로 돈 run에는 맨 끝에 `/ 시험용 정의·점수 아님`이 붙는다. semantic 조건이 다른 점수끼리, 정의 파일 sha256이 다른 semantic used 점수끼리는 견주지 않는다(새로 맞음·새로 틀림도 내지 않는다). semantic used 점수는 퓨어 조건 점수라고 부르지 않고, 원 논문·리더보드·스터디 E0 점수와 같은 줄에 두지 않는다.
- Olist 라벨: `Olist 스터디 평가셋 v0.3 / SQLite / 131eaf49 / 49문제 / EX(스터디 결과 비교) / evidence not_used / 로컬 측정 / <모델>·추론 <단계>·<백엔드> / 1회 실행 / 구현 Python <커밋>`(Olist의 evidence는 문항 조건이다. 문항 조건을 준 run이면 `evidence used`). `131eaf49`는 정답 파일의 sha256 앞 8자다. 꼬리표(미커밋 코드, 소표본·점수 아님, 가짜 백엔드·점수 아님)는 BIRD와 같다. 기본 기준 run은 없고 `report --against <run>`으로 견준다. 비교 문장은 부호 검정 p만 쓰고 "49문항·1회 실행이라 낫다·못하다로 읽지 않는다"를 붙인다. 스터디 E0 점수와 같은 줄에 두지 않는다.
- 누설 검사 한 줄이 0이 아니거나 도구 호출이 0이 아닌 run은 대표로 쓰지 않는다.
- 경로가 전체 하네스가 아닌 run의 라벨에는 `1회 실행` 뒤에 `/ 경로 single(단일 호출)` 또는 `/ 경로 partial(<바꾼 칸=값, …>)`이 붙는다. harness run의 라벨은 그대로다. 경로가 다른 run·기준선과 견주면 "경로가 달라 재현 비교 아님"이 붙는다.

## 모델 단위 경로(route)

- 프로필(`[profiles.<이름>]`)에 `route = "single" | "harness" | "partial"`과 근거 배열(`[[profiles.<이름>.route_basis]]`)을 둔다. `partial`은 `[profiles.<이름>.pipeline]`의 칸(`example_rows`, `value_search`, `column_profile`, `refine_rounds`)을 공통 `[pipeline]` 위에 덮어쓴다. `value_search = false`는 키워드 호출·값 검색·값 줄을, `column_profile = false`는 프로파일 줄을, `refine_rounds = 0`은 실행·고치기 반복을 건너뛴다. 끈 단계 밖의 프롬프트는 바이트 단위로 그대로다.
- 경로는 프로필을 이름(`--profile`)으로 골랐을 때 따른다. 대화로 모델 전체 이름과 추론 단계를 정했을 때(`--model`·`--effort`, 출처 conversation)는 참조 프로필 `p3-ref`와 backend auto를 뺀 프로필 가운데 백엔드·모델·추론 단계가 글자 그대로 같은 프로필을 찾아, 이름으로 고른 것과 같은 규칙으로 근거를 따진다. 근거가 맞는 프로필이 하나면 그 경로(`source` profile-match, 화면에 "프로필 <이름>(모델 이름으로 맞춤)"), 여럿이고 경로(partial이면 바꾼 칸까지)가 모두 같으면 파일에서 먼저 나오는 프로필(`route-apply`가 고르는 차례와 같다), 경로가 서로 다르면 전체 P3(`ambiguous`, 화면에 까닭과 프로필 이름들)다. 추론 단계를 말하지 않았으면(`--effort medium --effort-source default-medium`) 추론 단계를 기본값 medium으로 보고 같은 규칙으로 맞추며(`/newmodel`이 같은 규칙으로 재고 저장한다), 그 사실을 `route` 칸 `assumed_effort`("medium")와 화면 "경로 …" 줄 끝("추론 단계는 기본값 medium으로 봄")에 남긴다. 추론 단계의 출처가 그 밖(`config-default`·`session-env`)이면 맞추지 않는다(`no_effort`). 세션 모델·별칭·가짜 백엔드는 늘 전체 P3다. 측정(`route-start`)은 기록에 끌려가지 않게 두 경로를 강제한다. 프로필에 경로가 없거나, 근거가 없거나, 마지막 근거의 백엔드·모델·추론 단계가 프로필과 한 글자라도 다르면 전체 P3로 돌고 그 까닭을 `run.json`에 남긴다. 근거의 CLI 판·데이터셋·evidence 조건만 다르면 경로는 그대로 쓰고 "근거가 낡았다"·"잠정 경로" 경고를 화면·`run.json`·`status`·`report`에 남긴다(자동으로 다시 재지 않는다. 다시 재려면 `/newmodel`).
- 단일 호출 경로는 질문과 CREATE 원문만 주는 호출 하나다(틀과 응답 읽기는 형제 저장소 text2sql-model-baseline과 같다, `docs/provenance.md`). codex는 `bare` 인자(환경 블록·내장 스킬 목록만 끔), claude는 이 저장소의 고정 시스템 프롬프트를 쓴다. 밖으로 나가는 것은 질문과 CREATE 원문이다(evidence used면 문항 evidence 글 추가).
- 판정 규칙(`route-judge/1`): h = 하네스만 맞은 위치 수, s = 후보만 맞은 위치 수. BIRD는 h − s > 13이고 p < 0.05면 harness, s − h > 13이고 p < 0.05면 후보, 그 밖(차이 없음)은 더 싸므로 후보(후보의 토큰·문항 시간이 둘 다 하네스보다 크면 harness). Olist(49문항)는 p만 보고 p ≥ 0.05면 판정 보류(기록하지 않고 전체 P3 유지). 토큰·시간은 함께 보이되 판정 조건이 아니고, 시간은 두 run을 함께 돌려 잰 값이다. 판정은 1회 실행 측정으로 정한 설정값이며 다시 재면 바뀔 수 있다.
- 기록할 수 있는 판정: 같은 데이터셋·문항·문항 수, 소표본 아님, 가짜 백엔드 아님, 같은 백엔드·모델·추론 단계·evidence 방식, 같은 커밋이고 미커밋 아님, 멈춤 없음, 누설 0, 채점기 대조 맞음, DB 불변, 도구 호출 0, CLI 판이 두 run의 시작·끝에서 같음. `route-apply`는 근거를 배열에 더하기만 하고 참조 프로필 `p3-ref`에는 쓰지 않는다. `harness.toml`이 바뀌면 커밋하기 전의 run에는 `(미커밋 코드)`가 붙는다.

## Olist 평가셋 채점

`/getolist`로 받은 평가셋 v0.3(49문항)을 `/connect olist` 뒤 `/runset`으로 돌린다. 명령으로는 `uv run t2s run <모델 인자> --questions data/olist/model_inputs.v0.3.json`이며, 끝나면 `score`·`report`가 이어 돈다.

- 기본으로 모델에는 질문만 간다. 문항 조건(task_conditions)과 문항 종류는 주지 않는다(evidence not_used, AGENTS 규칙 9). 문항 종류가 sql이 아닌 항목이 있거나 칸이 다르면 읽지 않고 멈춘다.
- evidence used(`/runset`에 "문항 조건 넣어서"처럼 말했을 때만): 문항 조건 각 줄의 앞뒤 공백만 떼고 `"; "`로 이은 한 줄을 evidence로 준다. 새로 받을 파일은 없다. 채점은 같고, 채점 때 run에 준 글이 정답 파일의 문항 조건을 같은 규칙으로 이은 글과 같은지 대조한다.
- 채점 규칙은 스터디 채점 스크립트(`scripts/run_baseline.py`, 커밋 `290ff9be`)와 같다. 열 이름은 보지 않고 열 수·행 수는 같아야 한다. 열이 6개 이하면 열 순서의 모든 순열을 시도한다. 정답 파일의 행 순서 칸이 any·ignored·not_required·unordered일 때만 행 순서를 무시하고(중복은 보존), 그 밖은 순서대로 비교한다. 양쪽이 수면 차이가 문항의 허용 오차(없으면 0.0001) 안이면 같다. NULL은 NULL과만 같다.
- 예측 SQL은 끝 `;`를 떼고, 안에 `;`가 남으면 실행하지 않는다. 시간 제한 10초, 1000행을 넘으면 실패다. 읽기 전용(`mode=ro&immutable=1`)으로 열고 SELECT·READ·FUNCTION·RECURSIVE 밖의 동작과 load_extension을 거절한다.
- 정답 대조: 정답 SQL을 같은 규칙으로 돌려 정답 파일의 기대 결과와 같은지 본다. 하나라도 다르면 "정답 대조 False"이고 종료 코드 1이다.
- run 폴더의 `ex.jsonl`에는 문항별 0·1과 실행 이유 코드만 있고 난도는 빈 글이다. 이유 글은 `gold/_work/<run_id>/detail.jsonl`에만 있다.
- 예측 파일 채점(`score --predictions`)은 스터디 채점기에서 받지 않는다.

## evidence used로 돌리기

기본은 퓨어 조건(evidence not_used)이다. evidence used는 사용자가 말했을 때만 재는 별도 조건이다(AGENTS 규칙 9).

- 스킬: `/runset`에 "evidence 써서", "evidence 줘서", "조건 줘서", "문항 조건 넣어서", "힌트 줘서", "used로"처럼 말한다. 말이 없으면 not_used다.
- 명령: `uv run t2s run <모델 인자> --evidence used`(Olist면 `--questions data/olist/model_inputs.v0.3.json`도).
- BIRD: `/getbird`가 만든 공개 evidence 파일(`data/bird-minidev/hints.public.json`)을 쓴다. 이미 BIRD를 받았는데 이 파일이 없으면 `/getbird`를 한 번 더 부른다(다른 파일은 해시만 대조하고 건너뛴다). 공개 문항 전체에서만 쓰고 `--questions`와 함께 쓰지 않는다. `--limit N`이면 앞 N문항의 evidence만 준다.
- Olist: 모델 입력 파일의 문항 조건을 `"; "`로 이은 한 줄을 준다.
- `/connect`로 붙인 DB와 텍스트 문항에는 evidence가 없어 쓰임새 오류(종료 코드 2)로 멈춘다.
- 같은 evidence 글을 세 자리에 글자 그대로 넣는다: 키워드 프롬프트의 힌트 칸, 생성 프롬프트의 `-- External Knowledge:` 줄, 고치기 프롬프트의 지식 칸. 빈 evidence는 빈 글 그대로다. not_used run의 프롬프트는 이 갈래를 두기 전과 글자 하나 다르지 않다(`tests/unit/test_prompt_regression.py`).
- run을 시작하는 프로세스가 위치별 글을 `runs/<run_id>/hints.json`에 쓰고, 분리된 프로세스와 `resume`은 이 파일만 읽는다(run.json의 sha256과 대조). 이어 돌 때 evidence 방식은 run.json 그대로다.
- `leak-audit`은 그 문항의 evidence 글을 누설로 세지 않고, 정답 SQL·다른 문항 정답·프롬프트 누락·Olist 채점 메모는 그대로 센다. 빈 글이 아닌 evidence가 그 문항의 어느 프롬프트에도 없으면 "빠짐"으로 센다(0이 아니면 실패). 요약 줄은 `… 정답 SQL 0, evidence 제공(used)·빠짐 0, …`이다.
- evidence used 기준선은 두지 않는다. used run끼리는 `report --against <run>`으로 견준다.

## semantic used로 돌리기

기본은 semantic not_used다. semantic used는 사용자가 말했을 때만 쓰는 또 하나의 별도 조건이다(AGENTS 규칙 9). 지표 정의의 출처는 Graph MCP의 활성 모델 하나이고, 엔진은 Graph MCP를 부르지 않고 정의 파일 하나(꼴 `t2s-semantic/1`, `plugin/skills/p3-engine/references/semantic-file.md`)만 읽는다.

- 받기: 플러그인 설치본에서는 `/getmetrics`가 text2sql-plugin의 `t2s export`로 받고 `semantic-import`로 받아들인다. 이 저장소 클론에서는 text2sql-plugin 체크아웃에서 `uv run t2s export --out <이 저장소의 var/semantic-inbox/<이름>.json>`을 돌린 뒤 여기서 `uv run t2s semantic-import <이름>`(고지 뒤 `--yes`)을 돌린다. `t2s export`는 Graph MCP 서버에 토큰과 모델 이름만 보내고 DB의 내용은 보내지 않는다.
- 승인: 내보내기는 승인된 지표 가운데 승인된 연결만으로 계산되는 것만 싣고 뺀 것은 수만 적는다. 엔진은 정의 파일에 승인 전 항목이 하나라도 있으면 파일 전체를 받아들이지 않는다. 승인 상태는 내보낸 때의 것이라, 다시 받으려면 `/getmetrics`를 다시 부른다(저절로 다시 받지 않는다).
- 쓰기: 스킬은 `/text2sql`·`/runset`에 "지표 정의 넣어서", "지표 정의 써서", "semantic 써서", "시맨틱 써서", "semantic used로"처럼 말했을 때만 `--semantic used`를 붙인다. 명령은 `uv run t2s ask --semantic used <모델 인자> "<질문>"`, `uv run t2s run --semantic used <모델 인자>`. 받아들인 파일을 정해진 규칙(`dataset/semantic.py`의 규칙 판 1.0.0)으로 한 줄 글로 바꿔, evidence와 같은 세 자리(키워드 프롬프트의 힌트 칸, 생성 프롬프트의 `-- External Knowledge:` 줄, 고치기 프롬프트의 지식 칸)에 글자 그대로 넣는다. 단일 호출 경로는 이름표 줄 하나다. 같은 데이터셋의 모든 질문에 같은 글이 들어간다. semantic not_used의 프롬프트와 화면 글은 이 갈래를 두기 전과 글자 하나 다르지 않다.
- 거절(모두 모델을 부르기 전): 받아들인 정의 없음·BIRD Mini-Dev·`--evidence used`와 함께면 종료 코드 2, 받아들인 파일이나 DB가 받아들인 뒤 바뀌었으면 1("/getmetrics를 다시"). `route-start`에는 `--semantic`이 없고, semantic used run으로는 경로 판정을 하지 않는다.
- 화면: semantic used면 "밖으로 나감" 줄 끝에 `, 지표 정의 글`이 붙고 그 아래에 `지표 정의: 모델 <이름> 판 <8자> · 승인 지표 <N>개 · 받은 때 <날짜>` 한 줄이 나온다. `status`·`sessions`의 줄에는 `· semantic used`가 붙는다.
- 채점·보고·누설 검사: 채점은 run 폴더의 `semantic.json`이 `run.json`의 sha256과 같은지 보고 `score.json`에 `semantic` 칸을 남긴다. 기본 기준은 semantic not_used라 semantic used run의 `report`는 "비교 대상 없음(기준은 semantic not_used, 이번은 used라 견주지 않는다)"을 낸다. `leak-audit`은 정의 글이 들어 있는 것만 누설로 세지 않고, 정답 SQL·다른 문항 정답·문항 조건·채점 메모는 정의 글 안에 있어도 그대로 센다. 정의 글이 그 문항의 어느 프롬프트에도 없으면 "빠짐"으로 센다(0이 아니면 실패).
- `/connect`는 SQLite 하나를 붙인 뒤 그 DB에 맞는 활성 지표 모델이 있는지 알리기만 한다("지표 질문은 /ask로"). 정의를 받아들이지 않고 SQL 생성에 넣지 않는다.
- BIRD Mini-Dev에는 지표 모델이 없어 쓰지 않는다. Olist 평가셋 49문항은 semantic used로 채점할 수 있지만 따로 라벨을 붙이고 어떤 조건과도 견주지 않는다.

## TS 하네스와의 관계

이 하네스는 TS 하네스(text2sql-harness)의 P3 파이프라인을 Python으로 옮긴 것이다. 개발 중 TS 대표 run과 단계별 산출물·채점 결과가 같음을 모델 호출 없이 확인했다(예외 1곳, 드라이버 동작 차이). 달라진 동작은 `docs/provenance.md` 4절에 있다.

## 개발

- 검사: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`.
- 모듈 경계: `t2s.eval`(채점)을 import하는 것은 `cli/eval_cmds.py`뿐이고, 생성 쪽(`datasource`·`dataset`·`prep`·`pipeline`·`llm`·`runner`)은 정답 폴더를 읽거나 문자열로 가리키지 않는다. `tests/test_import_rules.py`가 이것을 검사하며, 이 테스트를 고쳐서 통과시키지 않는다. 'evidence'라는 글자는 겉(`cli/main.py`·`cli/run_cmds.py`의 인자·출력·run.json 칸)에만 쓰고, 안쪽 생성 패키지는 hint·knowledge라는 말만 쓴다(사용자가 승인한 예외 두 파일, `EVIDENCE_WORD_ALLOWED`). 공식 채점기 사본(`src/t2s/eval/vendor/`)은 고치지 않는다.
- 500문항 측정은 커밋한 깨끗한 작업 트리에서만 하고, 측정 중 `src/`를 바꾸지 않는다.

## 라이선스와 출처

`LICENSES/NOTICE` 요약.

- CHESS(Apache-2.0): 키워드 템플릿, 값 고르기·값 검색 규칙, 범주형 판정 한 줄을 복사 후 수정.
- DAMO-ConvAI(MIT): 스키마 글, 주석·결합 프롬프트를 복사 후 수정. `bird/README.md`의 CC BY-SA 4.0 문구는 `docs/provenance.md`에 인용해 두었다.
- MAC-SQL(라이선스 표기 없음): 코드·문장을 복사하지 않고 판정·고치기 동작만 다시 구현했다.
- BIRD Mini-Dev 자료는 CC BY-SA 4.0이고 저장소에 담지 않는다. 공식 채점기 두 파일(`src/t2s/eval/vendor/`)은 고치지 않고 담았으며, mini_dev 저장소 README 표기에 따라 CC BY-SA 4.0을 따른다.
- 이 저장소의 코드는 Apache License 2.0이다(`LICENSE`). 위 제3자 부분은 각자의 라이선스를 따른다.
