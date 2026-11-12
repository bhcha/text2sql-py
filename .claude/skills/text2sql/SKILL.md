---
name: text2sql
description: 지금 연결된 데이터셋에 자연어 질문 하나를 보내 SQL로 바꾸고 읽기 전용으로 실행해 결과를 보여 준다. 사용자가 /text2sql 질문을 부르거나 "이 DB에 물어봐", "SQL로 바꿔줘", "financial에서 … 몇 개야"라고 할 때 쓴다. 세션 모델이 직접 SQL을 쓰지 않고 t2s ask를 돌린다. 모델은 세션 모델이 기본이고 대화로 지정하면 그 모델이다. 데이터셋을 바꾸거나 새 DB를 붙이는 일은 /connect가 한다.
---

# text2sql — 질문 하나를 SQL로

사용자는 /text2sql 뒤에 질문만 말한다(BIRD면 DB 이름도). 이 스킬은 `t2s ask`를 한 번 돌리고 그 출력을 전달한다. 세션 모델(너)은 SQL을 직접 쓰지 않는다. 파이프라인 안의 호출만 모델을 쓴다. 모든 명령은 저장소 루트(`harness.toml`이 있는 폴더)에서 돌린다.

## 절차

1. 대상을 정한다. 대상은 지금 연결된 데이터셋이다. 사용자가 다른 데이터셋이나 새 DB 파일을 말하면 이 스킬에서 바꾸지 않고 "/connect로 먼저 바꿔 주세요"라고 알리고 멈춘다.
   - 소스가 하나인 데이터셋이면 `--source`를 붙이지 않는다.
   - BIRD처럼 소스가 여럿이면 사용자가 말한 DB 이름을 `--source <이름>`으로 붙인다(financial, card_games, california_schools, codebase_community, debit_card_specializing, european_football_2, formula_1, student_club, superhero, thrombosis_prediction, toxicology).
   - DB 이름을 말하지 않았으면 `uv run t2s datasets`로 지금 데이터셋을 보여 주고 어느 DB인지 한 줄로 묻는다. 추측하지 않는다.
2. 모델을 정한다(아래 "모델 정하기").
3. 실행한다. 질문 문장은 사용자가 쓴 그대로 따옴표 안에 넣는다(고치거나 번역하지 않는다):

   uv run t2s ask [--source <이름>] <모델 인자> "<질문>"

4. 종료 코드 0이면 출력을 그대로 보여 준다. 첫 줄(모델·추론·백엔드·출처), 대상, 밖으로 나가는 것, SQL, 실행 결과 앞 10행, 단계 요약, 호출·시간·토큰, DB 지문 전후, 실행 폴더가 들어 있다. 요약하거나 SQL을 고쳐 쓰지 않는다.
5. 종료 코드별로:
   - 2이고 "소스를 정해야 합니다"가 나오면 목록을 보여 주고 어느 것인지 묻는다.
   - 2이고 "모델을 알 수 없습니다"가 나오면 그 문장으로 되묻는다.
   - 5("준비 필요")면 `uv run t2s prepare`를 돌린 뒤(BIRD 첫 준비는 약 2분) 3번을 한 번만 다시 한다. `prepare`가 "자료 없음"으로 멈추면 "먼저 /getbird"라고 알리고 멈춘다.
   - 그 밖의 0이 아닌 코드면 마지막 오류 줄을 그대로 보여 주고 멈춘다. 같은 질문을 다시 돌리지 않는다(모델 호출마다 사용량이 든다).

## 모델 정하기 (사용자에게 인자를 묻지 않는다)

1. 사용자가 이번 대화에서 모델을 말했으면 그 모델로 돈다. 말 → 인자:
   - 오푸스, opus → `--backend claude-code --model claude-opus-5-5`
   - 테라, terra → `--backend codex-cli --model gpt-5.6-terra`
   - 솔, sol → `--backend codex-cli --model gpt-5.6-sol`
   - 루나, luna → `--backend codex-cli --model gpt-5.6-luna`
   - 아스트라, astra → `--backend codex-cli --model gpt-6-astra`
   - 그 밖의 전체 모델 이름은 그대로 `--model`에 넣는다. `claude-`로 시작하면 `--backend claude-code`, `gpt-`로 시작하면 `--backend codex-cli`.

   추론 단계를 말했으면(low·medium·high·xhigh·max) `--effort` 그 값, 말하지 않았으면 `--effort medium`. 조합이 gpt-5.6-terra·medium·codex-cli면 위 인자 대신 `--profile p3-ref`를 쓴다(원본 재현 조건). 이 경우들에 `--model-source conversation`을 붙인다.
2. 모델 말이 없으면 지금 네가 돌고 있는 모델을 `--model <전체 이름>`으로 반드시 명시하고 `--model-source session-self`를 붙인다.
   Claude Code 세션이면 `--backend claude-code`. 추론 단계는 적지 않는다(명령이 세션 설정에서 읽는다).
   Codex 세션이면 `--backend codex-cli`. 추론 단계를 알면 `--effort`로 적고, 모르면 적지 않는다(명령이 설정 파일 값을 쓴다).
3. 모델 이름을 알아듣지 못하면 돌리지 않고 한 줄로 되묻는다. 임의로 고르지 않는다.
4. 명령의 첫 줄(모델 · 추론 · 백엔드 · 출처)을 그대로 사용자에게 보여 준다. 그 줄이 사용자가 말한 모델과 다르면 멈추고 알린다.

## 하지 말 것

- SQL을 직접 쓰거나 결과를 손으로 고치지 않는다. 실행 폴더의 파일을 읽어 답을 바꾸지 않는다.
- DB 파일을 직접 열어 내용을 보거나 다른 도구로 스키마를 읽지 않는다.
- `gold/` 아래 파일과 `data/bird-minidev/download/` 아래 파일을 열어 보거나 내용을 출력하지 않는다.
- 새 DB를 여기서 등록하지 않는다. 등록은 밖으로 나가는 것을 알리고 승인을 받는 /connect의 일이다.
- `--backend`·`--model`·`--effort`·`--profile`·`--model-source`는 이 스킬과 명령 사이의 내부 계약이다. 사용자에게 치라고 요구하지 않는다.
