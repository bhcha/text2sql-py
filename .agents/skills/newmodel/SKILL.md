---
name: newmodel
description: 새 모델(백엔드·모델·추론 단계)이 나왔을 때 하네스(전체 P3)와 단일 호출(하네스 없음) 두 run을 같은 벤치마크(기본 BIRD Mini-Dev 500문항, evidence not_used)에서 함께 돌리고, 위치별로 견주어 그 모델에 하네스를 쓸지(route harness) 말지(route single)를 판정한 뒤, 사용자 승인을 받아 harness.toml 프로필에 경로와 근거를 더한다. 사용자가 $newmodel을 부르거나 "새 모델 재줘", "이 모델에 하네스 필요한지 봐줘", "하네스 쓸지 정해줘", "경로 판정", "판정 결과 기록해줘"라고 할 때 쓴다. 시작은 바로 돌아오고 진행·판정은 10~15줄로 보고한다. 모델은 세션 모델이 기본이고 대화로 지정하면 그 모델이다. run 파일 전체를 읽지 않는다.
---

# newmodel — 새 모델의 하네스 경로 정하기

사용자는 $newmodel만 부른다. 이 스킬은 `t2s route-start` → `t2s route-status` → `t2s route-judge` → (승인 뒤) `t2s route-apply`만 부르며 run 폴더의 파일을 열지 않는다. 모든 명령은 저장소 루트(`harness.toml`이 있는 폴더)에서 돌린다.

말 정리: 여기서 "단일 호출"은 하네스 없이 질문과 CREATE 원문만 주는 호출 하나다. AGENTS 규칙 9의 "퓨어 조건"(evidence not_used)과 다른 말이다. 두 run 모두 기본은 evidence not_used다.

## 무엇을 할지 고르기

- 시작 요청("재줘", "돌려줘", "새 모델", "앞 N문항만") → "시작".
- 진행 질문("어디까지", "진행") → "진행".
- 결과·판정 요청("판정", "결과", "하네스 쓸지") → "판정".
- 기록 승인("기록해줘", "harness.toml에 써줘", 보인 글에 "좋아") → "기록".
- 말이 없으면 먼저 `uv run t2s route-status`를 본다. "시작한 짝(route-start)이 없습니다"면 "시작", 진행 중이면 그 출력을 보여 준다, 마지막 줄이 "두 run 모두 채점 끝"이면 "판정"으로 간다.

## 시작

1. 준비를 확인한다:

   uv run t2s prepare

   "자료 없음"이면 "먼저 $getbird로 BIRD 자료를 받아야 합니다"라고 알리고 멈춘다.
2. 모델을 정한다(아래 "모델 정하기").
3. evidence 방식을 정한다. 기본은 not_used다. 사용자가 이번 대화에서 evidence(문항 조건)를 주라고 말했을 때만 used로 돌린다(`--evidence used`, 두 run 모두). used 판정은 not_used 판정과 따로 기록되고 서로 견주지 않는다.
4. 규모를 한 줄로 알린다. BIRD 전체면 "BIRD 500문항을 하네스와 단일 호출로 함께 돌립니다. 하네스 약 1,000~1,500회, 단일 호출 500회 호출입니다. 두 run을 함께 돌리므로 사용량 한도에 더 빨리 닿을 수 있습니다." 묻지 않고 바로 시작한다.
5. 시작한다:

   uv run t2s route-start <모델 인자> [--limit N] [--evidence used]

   - "앞 N문항만"이면 `--limit N`. 소표본 판정은 기록할 수 없다(명령이 거절한다).
   - 지금 데이터셋이 `olist`면 늘 `--questions data/olist/model_inputs.v0.3.json`을 붙인다. 파일이 없으면 "먼저 $getolist로 스터디 평가셋을 받아야 합니다"라고 알리고 멈춘다(파일 존재만 보고 열지 않는다).
   - "이미 도는 run이 있습니다"가 나오면 새로 시작하지 않고 그 줄을 보여 준다.
   - "모델 호출 확인 실패"가 나오면 그 줄을 그대로 보여 주고 멈춘다.
6. 출력을 그대로 보여 준다(모델 줄, 두 경로 줄, 밖으로 나가는 것, 시작 줄 둘, 짝 이름 줄). 명령은 분리된 프로세스 둘을 띄우고 바로 돌아온다. 기다리거나 route-status를 되풀이해 부르지 않는다.

## 진행

   uv run t2s route-status

출력을 그대로 보여 준다. 한 run이 "멈춤(사용량 한도)"이면 한도가 풀린 뒤 그 run을 이어 돌릴 수 있다고 알리고, 사용자가 좋다고 하면 그 run 이름으로 `uv run t2s resume --run-id <이름>`을 부른다(이름은 route-status 출력의 run 이름 그대로).

## 판정

   uv run t2s route-judge

출력을 그대로 보여 준다. 두 run의 라벨 전체와 "새로 맞음·새로 틀림" 문장, 비용 줄, 경로 판정 줄을 자르지 않는다.

- "아직 생성 중입니다"가 나오면 route-status 출력을 보여 준다.
- "아직 채점 전입니다: t2s score --run <이름>"이 나오면 그 이름으로 `uv run t2s score --run <이름>`을 한 번 돌린 뒤(약 2~3분, 명령 시간 제한을 10분으로 준다) route-judge를 다시 부른다.
- "evidence 조건이 달라 견주지 않는다"가 나오면 그대로 보여 주고 멈춘다.
- "기록에 쓸 수 없음: …"이 나오면 그 줄을 그대로 보여 주고 기록하지 않는다(소표본, 가짜 백엔드, 미커밋 코드, 누설, 판정 보류 등).
- Olist 판정은 p만 쓰고, p ≥ 0.05면 "판정 보류"다. 보류면 harness.toml에 쓰지 않고 전체 P3를 유지한다고 알린다.
- 판정은 1회 실행 측정으로 정한 설정값이다. "어느 모델이 낫다", 원 논문·리더보드 점수를 덧붙이지 않는다.

## 기록

1. 더할 글을 본다(쓰지 않는다):

   uv run t2s route-apply

2. 출력(프로필 이름, 새 프로필인지, 더하는·바꾸는 줄)을 그대로 보여 주고 "harness.toml에 이대로 쓸까요?"라고 한 줄로 묻는다. 사용자가 다른 프로필 이름을 말했으면 `--profile <이름>`을 붙여 1을 다시 한다.
3. 사용자가 승인했을 때만 쓴다:

   uv run t2s route-apply --yes

4. 쓴 뒤 "harness.toml이 바뀌었습니다. 커밋하기 전의 run에는 (미커밋 코드)가 붙습니다. 커밋은 말씀하실 때 합니다"를 알린다. 커밋하지 않는다.
5. 경로는 그 프로필을 이름으로 골랐을 때만 따른다(`$runset`·`$text2sql`에서 "<프로필 이름>으로"라고 말할 때). 세션 모델·별칭·모델 이름으로 정한 run은 늘 전체 P3다.

## 모델 정하기 (사용자에게 인자를 묻지 않는다)

1. 사용자가 이번 대화에서 모델을 말했으면 그 모델로 돈다. 말 → 인자:
   - 오푸스, opus → `--backend claude-code --model claude-opus-5-5`
   - 테라, terra → `--backend codex-cli --model gpt-5.6-terra`
   - 솔, sol → `--backend codex-cli --model gpt-5.6-sol`
   - 루나, luna → `--backend codex-cli --model gpt-5.6-luna`
   - 아스트라, astra → `--backend codex-cli --model gpt-6-astra`
   - 그 밖의 전체 모델 이름은 그대로 `--model`에 넣는다. `claude-`로 시작하면 `--backend claude-code`, `gpt-`로 시작하면 `--backend codex-cli`.

   추론 단계를 말했으면(low·medium·high·xhigh·max) `--effort` 그 값, 말하지 않았으면 `--effort medium`. 이 경우들에 `--model-source conversation`을 붙인다. 판정과 기록은 백엔드·모델·추론 단계를 글자 그대로 쓰므로 모델 이름을 줄이거나 바꾸지 않는다.
2. 모델 말이 없으면 지금 네가 돌고 있는 모델을 `--model <전체 이름>`으로 반드시 명시하고 `--model-source session-self`를 붙인다. Claude Code 세션이면 `--backend claude-code`, Codex 세션이면 `--backend codex-cli`(추론 단계를 알면 `--effort`로 적는다).
3. 모델 이름을 알아듣지 못하면 돌리지 않고 한 줄로 되묻는다. 임의로 고르지 않는다.
4. 명령의 첫 줄(모델 · 추론 · 백엔드 · 출처)이 사용자가 말한 모델과 다르면 `uv run t2s stop --run-id <이름>`으로 두 run을 멈추고 알린다.

## 하지 말 것

- run 폴더의 `records.jsonl`, `report.md`, `report.json`, `ex.jsonl`, 판정 파일, `items/` 아래 파일을 읽지 않는다. 명령 출력만 쓴다.
- `gold/` 아래 파일과 공개 evidence 파일을 열거나 내용을 출력하지 않는다.
- 사용자 승인 없이 `--yes`를 붙이지 않는다. harness.toml을 손으로 고치지 않는다.
- 두 run이 도는 동안 `src/` 아래를 바꾸는 작업을 함께 하지 않는다.
- 참조 프로필 `p3-ref`에는 쓰지 않는다(명령이 거절한다).
- 점수 라벨을 줄여 쓰지 않는다. 원 논문·리더보드 점수, 스터디 E0 점수를 덧붙이지 않는다.
- `--backend`·`--model`·`--effort`·`--model-source`·`--limit`·`--questions`·`--evidence`·`--profile`·`--run-id`·`--yes`는 이 스킬과 명령 사이의 내부 계약이다. 사용자에게 치라고 요구하지 않는다.
