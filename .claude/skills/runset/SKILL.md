---
name: runset
description: 지금 연결된 데이터셋의 문항 전체(또는 "앞 N문항만")를 실제 모델로 돌린다. 정답이 있는 데이터셋이면 채점한다(BIRD Mini-Dev는 공식 채점기로 채점하고 기준 run과 위치별로 비교, Olist는 스터디 평가셋 v0.3 49문항을 스터디 채점 규칙으로 채점). 정답이 없으면 SQL과 실행 상태만 남긴다. 사용자가 /runset을 부르거나 "전체 돌려줘", "BIRD 500문항 재줘", "Olist 49문항 돌려줘", "진행 어때", "결과 보고해줘"라고 할 때 쓴다. start는 바로 돌아오고, status·report는 10줄 안팎으로 보고한다. 모델은 세션 모델이 기본이고 대화로 지정하면 그 모델이다. run 파일 전체를 읽지 않는다.
---

# runset — 지금 데이터셋 전체 실행

사용자는 /runset만 부른다. 이 스킬은 `t2s run`(start) → `t2s status` → `t2s report`만 부르며 run 폴더의 `records.jsonl`·`report.md`·`items/`를 열지 않는다(문항 수백 개가 세션 맥락을 채운다). 모든 명령은 저장소 루트(`harness.toml`이 있는 폴더)에서 돌린다.

## 무엇을 할지 고르기

- 시작 요청("돌려줘", "전체", "앞 N문항만", "소표본") → "시작".
- 진행 질문("어디까지", "진행", "status") → "진행".
- 결과 요청("보고", "결과", "점수", "report") → "보고".
- 멈춤 요청 → `uv run t2s stop`.
- 이어 돌리기 요청 → `uv run t2s resume`. 거절 까닭이 나오면 그대로 보여 준다.
- 말이 없으면 먼저 `uv run t2s status`를 본다. 첫 줄이 "진행 중"이면 그 출력을 보여 준다. "끝남"이면 "보고"로 간다. "시작한 run이 없습니다"이거나 이미 보고까지 한 run이면 "시작"으로 간다.

## 시작

1. 준비를 확인한다(준비된 것은 건너뛰므로 여러 번 불러도 된다. BIRD 첫 준비는 약 2분):

   uv run t2s prepare

   "자료 없음"으로 멈추면 "먼저 /getbird로 BIRD 자료를 받아야 합니다"라고 알리고 멈춘다. "임베딩 모델이 없다"로 멈추면 "모델 파일 417MB를 받아야 합니다"라고 알리고, 사용자가 좋다고 하면 `uv run t2s fetch model` 뒤 다시 한다.
2. 모델을 정한다(아래 "모델 정하기").
3. evidence 방식을 정한다(아래 "evidence 정하기").
4. 규모를 한 줄로 알린다. BIRD 전체면 "500문항, 약 1,000~1,500회 호출, 30~70분 예상입니다. evidence not_used(또는 used)로 돕니다.", Olist면 "49문항, 약 100~150회 호출입니다. evidence not_used(또는 used)로 돕니다." 묻지 않고 바로 시작한다(/runset을 부른 것이 시작 요청이다).
5. 시작한다:

   uv run t2s run <모델 인자> [--limit N] [--evidence used]

   - "앞 N문항만", "N개만"이면 `--limit N`. "소표본"이면 `--limit 20`. 아니면 `--limit` 없이 지금 데이터셋 전체.
   - evidence used로 정했으면 `--evidence used`를 붙인다. not_used면 붙이지 않는다(기본이 not_used다).
   - 지금 데이터셋이 `olist`면(1단계 `prepare` 출력이나 `uv run t2s datasets`의 "지금 바라보는 데이터셋" 줄) 늘 `--questions data/olist/model_inputs.v0.3.json`을 붙인다. 이 파일로 돌린 run만 스터디 채점기가 채점한다. 파일이 없으면 "먼저 /getolist로 스터디 평가셋을 받아야 합니다"라고 알리고 멈춘다(파일 존재만 보고 열지 않는다). 문항 조건(task_conditions)은 말이 없으면 주지 않는다(evidence not_used). 사용자가 문항 조건을 주라고 말했을 때만 `--evidence used`를 붙인다.
   - "먼저 /getbird(다시 부르면 evidence 파일을 만든다)"가 나오면 그 줄을 보여 주고 멈춘다. 이미 BIRD를 받은 사람도 /getbird를 한 번 더 부르면 공개 evidence 파일이 생긴다.
   - "이 문항에는 evidence가 없습니다"가 나오면(/connect로 붙인 DB, 텍스트 문항) evidence used로 돌 수 없다고 알리고 멈춘다.
   - "이 데이터셋에는 문항 파일이 없습니다"가 나오면(/connect로 붙인 DB) 질문이 한 줄에 하나씩 든 텍스트 파일 경로를 한 줄로 묻고, 받은 경로를 `--questions <경로>`로 붙여 다시 한다.
   - "이미 도는 run이 있습니다"가 나오면 새로 시작하지 않고 "진행"으로 간다.
   - "모델 호출 확인 실패"가 나오면 그 줄을 그대로 보여 주고 멈춘다(분리 실행 전의 짧은 확인 호출이 실패한 것이다. 사용량 한도면 풀린 뒤 다시 부른다).
6. 출력을 그대로 보여 준다(첫 줄: 모델·추론·백엔드·출처, 밖으로 나가는 것, 시작 줄). evidence used면 밖으로 나가는 것 줄에 "문항 evidence 글"이 붙고, 시작 줄 끝에 evidence 방식이 붙는다. 명령은 분리된 프로세스를 띄우고 바로 돌아온다. 끝날 때까지 기다리거나 status를 되풀이해 부르지 않는다. 사용자가 물을 때만 status를 부른다.

## 진행

   uv run t2s status

출력을 그대로 보여 준다. 첫 줄이 "끝남"이면 "보고"로 간다. "멈춤(사용량 한도)"이면 한도가 풀린 뒤 이어 돌릴 수 있다고 알린다.

## 보고

   uv run t2s report

출력을 그대로 보여 준다. 라벨 전체 문자열과 "새로 맞음·새로 틀림" 문장을 자르지 않는다.

- Olist run이면 셋째 줄이 "정답 대조"(정답 SQL을 같은 규칙으로 돌려 기대 결과와 같은지)다. 비교 대상(기준 run)이 없으면 "비교 대상 없음"이 맞는 출력이다. 스터디 E0 점수와 같은 줄에 두지 않는다.
- evidence used run이면 기본 기준(evidence not_used 기준선)과 견주지 않으므로 "비교 대상 없음(기준은 evidence not_used, 이번은 used라 견주지 않는다)"이 맞는 출력이다. "evidence 조건이 달라 견주지 않는다"가 나오면 그대로 보여 준다. evidence 방식이 다른 run끼리는 우열을 말하지 않는다.

- "아직 생성 중입니다"가 나오면 status 출력을 보여 준다.
- "아직 채점 전입니다"가 나오면 `uv run t2s score`를 한 번 돌린 뒤(약 2~3분, 명령 시간 제한을 10분으로 준다) report를 다시 부른다.
- "정답 없음: 채점 생략"이 나오면 그 아래 실행 요약을 그대로 보여 준다. 채점을 시도하지 않는다.
- 다음을 덧붙이지 않는다: 원 논문·리더보드 점수, 스터디 E0 점수, "TS보다 낫다/못하다", "어느 모델이 낫다".

## evidence 정하기 (사용자에게 인자를 묻지 않는다)

- 기본은 evidence not_used(퓨어 조건)다. BIRD·Olist 둘 다 같다.
- 사용자가 이번 대화에서 "evidence 써서", "evidence 줘서", "조건 줘서", "문항 조건 넣어서", "힌트 줘서", "used로"처럼 evidence(문항 조건)를 주라고 말했을 때만 evidence used로 돈다. 말이 없으면 not_used다.
- "둘 다"라고 하면 어느 것부터 돌릴지 한 줄로 묻는다(한 번에 run 하나만 돈다).
- evidence used run에서는 문항의 evidence 글(BIRD 공식 evidence, Olist 문항 조건)도 모델 제공자 서버로 나간다.
- used 점수는 퓨어 조건 점수라고 부르지 않는다. evidence 방식이 다른 run끼리 우열을 말하지 않는다.

## 모델 정하기 (사용자에게 인자를 묻지 않는다)

1. 사용자가 이번 대화에서 모델을 말했으면 그 모델로 돈다. 말 → 인자:
   - 오푸스, opus → `--backend claude-code --model claude-opus-5-5`
   - 테라, terra → `--backend codex-cli --model gpt-5.6-terra`
   - 솔, sol → `--backend codex-cli --model gpt-5.6-sol`
   - 루나, luna → `--backend codex-cli --model gpt-5.6-luna`
   - 아스트라, astra → `--backend codex-cli --model gpt-6-astra`
   - 그 밖의 전체 모델 이름은 그대로 `--model`에 넣는다. `claude-`로 시작하면 `--backend claude-code`, `gpt-`로 시작하면 `--backend codex-cli`.

   추론 단계를 말했으면(low·medium·high·xhigh·max) `--effort` 그 값, 말하지 않았으면 `--effort medium --effort-source default-medium`(추론 단계를 말하지 않았다는 표시. 이때는 아래의 모델 이름 맞추기를 하지 않는다). 조합이 gpt-5.6-terra·medium·codex-cli면 위 인자 대신 `--profile p3-ref`를 쓴다(원본 재현 조건). 이 경우들에 `--model-source conversation`을 붙인다.

   사용자가 harness.toml의 프로필 이름을 말했으면("p3-claude로", "<이름> 프로필로") 위 인자 대신 `--profile <이름>`과 `--model-source conversation`을 쓴다. 프로필의 경로(single·harness·partial, /newmodel로 정한 것)는 프로필을 이름으로 골랐을 때 따르고, 모델 이름과 추론 단계를 말했을 때도 백엔드·모델·추론 단계가 글자 그대로 같고 근거가 맞는 프로필이 있으면 따른다(명령이 맞춘다. 맞는 프로필 여럿의 경로가 다르면 전체 P3). 추론 단계를 말하지 않았거나 세션 모델이면 전체 P3다. `p3-ref`는 경로를 갖지 않으므로 위의 p3-ref 규칙은 그대로다. 첫 줄 아래에 "경로 …" 줄이 나오면 그대로 보여 준다.
2. 모델 말이 없으면 지금 네가 돌고 있는 모델을 `--model <전체 이름>`으로 반드시 명시하고 `--model-source session-self`를 붙인다.
   Claude Code 세션이면 `--backend claude-code`. 추론 단계는 적지 않는다(명령이 세션 설정에서 읽는다).
   Codex 세션이면 `--backend codex-cli`. 추론 단계를 알면 `--effort`로 적고, 모르면 적지 않는다(명령이 설정 파일 값을 쓴다).
3. 모델 이름을 알아듣지 못하면 돌리지 않고 한 줄로 되묻는다. 임의로 고르지 않는다.
4. 명령의 첫 줄(모델 · 추론 · 백엔드 · 출처)을 그대로 사용자에게 보여 준다. 그 줄이 사용자가 말한 모델과 다르면 `uv run t2s stop`으로 멈추고 알린다.

세션 모델이 gpt-5.6-terra·medium이 아니면 그 run은 TS 원본 재현 비교가 아니다. 검증 측정은 사용자가 "terra medium으로"라고 말할 때만 해당한다.

## 하지 말 것

- run 폴더의 `records.jsonl`, `report.md`, `report.json`, `ex.jsonl`, `items/` 아래 파일을 읽지 않는다. status·report 출력만 쓴다.
- `gold/` 아래 파일을 열거나 내용을 출력하지 않는다.
- run이 도는 동안 `src/` 아래를 바꾸는 작업을 함께 하지 않는다.
- 같은 run을 두 번 시작하지 않는다. 멈춘 run은 resume으로 잇는다. 채점한 run은 이어 돌릴 수 없다.
- 점수 라벨을 줄여 쓰지 않는다.
- `--backend`·`--model`·`--effort`·`--profile`·`--model-source`·`--limit`·`--questions`·`--evidence`는 이 스킬과 명령 사이의 내부 계약이다. 사용자에게 치라고 요구하지 않는다.
