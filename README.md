# text2sql-py

P3 text-to-SQL 파이프라인(11단계)을 Python으로 다시 만든 하네스다. SQLite DB에 자연어로 묻고, 문항 묶음을 한꺼번에 돌리고, 정답이 있으면(BIRD Mini-Dev) 공식 채점기로 잰다. 규칙은 `AGENTS.md`, 출처는 `docs/provenance.md`에 있다.

## 먼저 알 것

- 밖으로 나가는 것: 질문, CREATE 원문과 예시 3행, 질문과 비슷한 값, 열 프로파일(범주형 열은 값 전부), 실행 오류 글이 모델 제공자 서버(OpenAI 또는 Anthropic)로 나간다. 정답 SQL은 나가지 않는다. 민감한 DB에 쓰지 않는다.
- DB는 읽기 전용으로만 연다(`mode=ro&immutable=1`, 쓰기 거절, 실행 전후 sha256 비교). 다른 프로그램이 그 DB에 쓰고 있지 않아야 한다.
- 모델 호출은 이 기기의 `codex` 또는 `claude` 명령으로 한다(구독 사용량이 든다). 500문항은 약 1,000~1,500회 호출, 30~70분이다.

## 필요한 것

- macOS, uv, git, curl. Python 3.13.7은 uv가 받는다. 첫 `uv run`이 실행 의존(numpy·onnxruntime·tokenizers)과 채점 의존(func-timeout·psycopg2-binary·PyMySQL)을 깐다.
- `codex` 또는 `claude` 명령(로그인된 상태).
- 디스크 약 3GB(BIRD zip 0.8GB, 푼 DB 1.4GB, 임베딩 모델 0.4GB).

## 스킬로 쓰기 (Claude Code는 /이름, Codex는 $이름)

| 차례 | 스킬 | 하는 일 | 걸리는 시간 |
|---|---|---|---|
| 1 | `/getbird` | BIRD Mini-Dev 자료와 임베딩 모델을 받고 해시를 대조, 정답은 `gold/`로 | 받기 1.2GB |
| 2 | `/connect` | 데이터셋 목록·준비·전환. `/connect bird`, `/connect <SQLite 경로>`(고지 뒤 승인), `/connect <이름>` | BIRD 준비 약 2분 |
| 3 | `/text2sql <질문>` | 지금 데이터셋에 질문 하나. BIRD면 DB 이름을 함께 말한다 | 수 초~수십 초 |
| 4 | `/runset` | 지금 데이터셋 전체(또는 "앞 N문항만")를 돌리고, 정답이 있으면 채점·기준 비교 | 500문항 30~70분 |

- `/getbird` 뒤 바로 `/runset`을 불러도 된다(스킬이 `prepare`를 먼저 돌린다). 기본 데이터셋이 `bird-minidev`라서 `/connect bird`는 건너뛰어도 된다. `bird`는 데이터셋 `bird-minidev`를 가리키는 말이다.
- BIRD의 DB 이름 11개: financial, card_games, california_schools, codebase_community, debit_card_specializing, european_football_2, formula_1, student_club, superhero, thrombosis_prediction, toxicology(`uv run t2s datasets`로도 본다).

스킬이 부르는 명령(스킬을 쓰지 않을 때 같은 일을 하는 차례):

| 스킬 | 명령 |
|---|---|
| `/getbird` | `fetch bird` |
| `/connect bird` | `prepare bird-minidev` → `use bird-minidev` |
| `/connect <경로>` | `register <이름> --sqlite <경로>`(고지) → 승인 뒤 `--yes` → `prepare <이름>` → `use <이름>`. 이름은 말한 것, 없으면 파일 이름에서 만든다 |
| `/text2sql <질문>` | `ask [--source <DB>] <모델 인자> "<질문>"`. 종료 코드 5면 `prepare` 뒤 한 번 더 |
| `/runset` | `status`로 할 일을 고른 뒤 `prepare` → `run <모델 인자> [--limit N] [--questions <파일>]` → (물을 때) `status` → `report` |
- `/runset`은 시작하고 바로 돌아온다. "진행 어때"로 상태를, 끝난 뒤 "보고"로 결과를 본다.
- 모델은 지금 세션의 모델이 기본이다. "terra medium으로", "opus로"처럼 말하면 그 모델을 쓴다. 모델을 알 수 없으면 돌리지 않고 묻는다.

## 명령으로 쓰기

모든 명령은 저장소 루트(`harness.toml`이 있는 폴더)에서 `uv run t2s <명령>`으로 돌린다. 다른 루트를 쓰려면 `T2S_ROOT`를 준다.

| 명령 | 하는 일 | 보기 |
|---|---|---|
| `fetch bird` | BIRD 자료·정답·임베딩 모델 받기와 해시 대조 | `uv run t2s fetch bird` |
| `fetch model` | 임베딩 모델만 받기 | `uv run t2s fetch model` |
| `datasets` | 데이터셋 목록, 지금 바라보는 것, 준비 상태 | `uv run t2s datasets` |
| `use` | 바라볼 데이터셋 바꾸기 | `uv run t2s use shop` |
| `register` | SQLite 파일을 데이터셋으로 등록(고지 뒤 `--yes`) | `uv run t2s register shop --sqlite ~/db/shop.sqlite --yes` |
| `prepare` | 값 색인·프로파일 만들기(준비된 것은 건너뜀) | `uv run t2s prepare` |
| `ask` | 질문 하나 → SQL → 읽기 전용 실행 → 앞 10행 | `uv run t2s ask --source financial --profile p3-ref "How many accounts are there?"` |
| `run` | 문항 묶음 실행(분리 실행, 끝나면 채점·보고까지) | `uv run t2s run --profile p3-ref --limit 20` |
| `status` | 가장 최근(또는 `--run-id`) run의 진행, 10줄 안 | `uv run t2s status` |
| `stop` | 도는 run 멈추기 | `uv run t2s stop` |
| `resume` | 멈춘 run 이어 돌기(채점 전, 같은 커밋일 때만) | `uv run t2s resume` |
| `score` | 공식 채점기로 채점, 또는 예측 파일을 받아 채점 | `uv run t2s score --run <id>` |
| `report` | 라벨, EX, 기준과 위치별 비교, 누설 검사 한 줄 | `uv run t2s report` |
| `leak-audit` | 프롬프트에 정답·evidence가 들어갔는지 검사 | `uv run t2s leak-audit --run <id>` |

`ask`·`run`은 스스로 준비하지 않는다. 자산이 없거나 낡았으면 모델을 부르기 전에 종료 코드 5로 멈추므로 `prepare`를 먼저 돌린다. 소스가 하나인 데이터셋이면 `ask`에 `--source`를 붙이지 않는다. 모델 인자가 없으면 세션 기록에서 모델을 찾고, 못 찾으면 종료 코드 2로 멈춘다. 정답이 없는 데이터셋에서 `run`은 0으로 끝나고(생성 뒤 이어 부른 채점만 4), `score`·`leak-audit`은 4다.

모델 인자(`--profile`, `--backend`, `--model`, `--effort`, `--model-source`, `--effort-source`, `--session`)는 스킬이 넘기는 내부 계약이다. 손으로 칠 때는 `--profile p3-ref`(codex-cli·gpt-5.6-terra·medium), `--backend claude-code --model claude-opus-5-5`, 모델 호출 없는 시험용 `--backend fake`를 쓰면 된다.

종료 코드: 0 성공, 1 실패, 2 쓰임새 오류(소스·모델을 정해야 함), 3 승인 필요(`register`), 4 정답 없음(채점 생략), 5 준비 필요(`prepare`를 먼저).

## 자기 DB에 묻기

1. `/connect <SQLite 경로>`. 밖으로 나가는 것을 알리고 승인을 받은 뒤 등록 → 준비 → 전환한다.
2. `/text2sql <질문>`. 소스가 하나라 DB 이름은 말하지 않아도 된다.
3. 문항 묶음을 돌리려면 질문이 한 줄에 하나씩 든 텍스트 파일을 만들고 `/runset`에 그 경로를 알려 준다(명령으로는 `uv run t2s run --questions <파일>`). 빈 줄과 `#`로 시작하는 줄은 건너뛴다.
4. 정답이 없으므로 채점은 없다. `report`는 "정답 없음: 채점 생략"과 실행 요약(상태 수, 실행 상태 수, 고치기 수)을 내고, SQL과 실행 상태는 `runs/<run_id>/records.jsonl`에 남는다.

## 결과가 놓이는 곳

모두 git 밖이다.

- `runs/<run_id>/`: `run.json`(시작 조건: 모델, 커밋, 판, DB 지문), `control.json`(진행 상태), `run.log`, `items/<위치>/`(`record.json`, `trace.json`, 호출마다 `calls/<k>/prompt.txt`·`response.txt`·`llm-meta.json`·`cli-events.jsonl`), `records.jsonl`, `predictions.json`, `manifest.json`, 채점 뒤 `score.json`·`ex.jsonl`·`evaluator-stdout.txt`·`leak-audit.json`, 보고 뒤 `report.json`·`report.md`.
- `runs/ask/<시각>-<16진>/`: `ask` 한 번의 `trace.json`·`ask.json`·`calls/`.
- `data/`: 받은 자료, 임베딩 모델, 자산(`data/assets/<데이터셋>/<소스>/`의 값 색인·프로파일·manifest).
- `gold/`: 정답과 정답 설정, 채점 중간 파일(`gold/_work/<run_id>/`). 채점(`score`·`report`·`leak-audit`)만 읽는다. 생성 쪽(`ask`·`run`)은 읽기 감시를 걸고 돌아 이 폴더를 열지 못한다.
- `var/state.json`: 지금 바라보는 데이터셋과 등록 기록.

## 점수를 읽는 법

- 모든 점수에 라벨 전체가 붙는다. 예: `BIRD Mini-Dev / SQLite / cc48ba16 / 500문제 / EX / evidence not_used / 로컬 측정 / gpt-5.6-terra·추론 medium·codex-cli / 1회 실행 / 구현 Python <커밋>`. `cc48ba16`은 BIRD zip의 sha256 앞 8자다. 미커밋 코드면 `/ (미커밋 코드)`, 앞 N문항만이면 `/ 소표본·점수 아님`, 가짜 백엔드면 `/ 가짜 백엔드·점수 아님`이 붙는다. 라벨을 줄여 쓰지 않는다.
- 기준(TS 기준선 `baselines/ts-pure-fix.json`, EX 263/500)과 위치별로 견주어 새로 맞음·새로 틀림을 함께 적는다. 차이가 13 이하이거나 부호 검정 p가 0.05 이상이면 "차이 없음(변동 범위)"이다. 같은 코드도 run마다 수십 위치가 갈리므로 1회 실행으로 낫다·못하다를 말하지 않는다.
- 참조 조건은 codex-cli·gpt-5.6-terra·medium이다. 다른 모델이면 "재현 비교 아님", 백엔드가 다르면 우열을 말하지 않는다.
- 원 논문·리더보드 점수와 견주지 않는다(측정 조건이 다르다).
- 누설 검사 한 줄이 0이 아니거나 도구 호출이 0이 아닌 run은 대표로 쓰지 않는다.

## TS 하네스와의 관계

이 하네스는 TS 하네스(text2sql-harness)의 P3 파이프라인을 Python으로 옮긴 것이다. 개발 중 TS 대표 run과 단계별 산출물·채점 결과가 같음을 모델 호출 없이 확인했다(예외 1곳, 드라이버 동작 차이). 달라진 동작은 `docs/provenance.md` 4절에 있다.

## 개발

- 검사: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`.
- 모듈 경계: `t2s.eval`(채점)을 import하는 것은 `cli/eval_cmds.py`뿐이고, 생성 쪽(`datasource`·`dataset`·`prep`·`pipeline`·`llm`·`runner`)은 정답 폴더를 읽거나 문자열로 가리키지 않는다. `tests/test_import_rules.py`가 이것을 검사하며, 이 테스트를 고쳐서 통과시키지 않는다. 공식 채점기 사본(`src/t2s/eval/vendor/`)은 고치지 않는다.
- 500문항 측정은 커밋한 깨끗한 작업 트리에서만 하고, 측정 중 `src/`를 바꾸지 않는다.

## 라이선스와 출처

`LICENSES/NOTICE` 요약.

- CHESS(Apache-2.0): 키워드 템플릿, 값 고르기·값 검색 규칙, 범주형 판정 한 줄을 복사 후 수정.
- DAMO-ConvAI(MIT): 스키마 글, 주석·결합 프롬프트를 복사 후 수정. `bird/README.md`의 CC BY-SA 4.0 문구는 `docs/provenance.md`에 인용해 두었다.
- MAC-SQL(라이선스 표기 없음): 코드·문장을 복사하지 않고 판정·고치기 동작만 다시 구현했다.
- BIRD Mini-Dev 자료는 CC BY-SA 4.0이고 저장소에 담지 않는다. 공식 채점기 두 파일(`src/t2s/eval/vendor/`)은 고치지 않고 담았으며, mini_dev 저장소 README 표기에 따라 CC BY-SA 4.0을 따른다.
- 이 저장소의 코드는 Apache License 2.0이다(`LICENSE`). 위 제3자 부분은 각자의 라이선스를 따른다.
