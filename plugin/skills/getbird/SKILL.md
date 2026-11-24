---
name: getbird
description: BIRD Mini-Dev 자료(공식 zip 0.8GB, SQLite DB 11개, 정답·문제 파일)와 값 검색용 임베딩 모델(417MB)을 원격에서 받아 sha256으로 대조하고, 정답 파일을 .text2sql/gold/로 떼어 놓는다. 작업 폴더에 BIRD 자료가 없을 때, p3 datasets가 "자료 없음"을 보일 때, 사용자가 /getbird를 부르거나 "BIRD 데이터 받아줘"라고 할 때 쓴다. 인자 없음, 여러 번 불러도 안전하다.
---

# getbird — BIRD 자료와 모델 받기

사용자는 인자 없이 /getbird만 부른다. 아래 명령 하나만 실행하고 그 요약을 전달한다.

준비: 이 스킬은 엔진 스킬의 실행 스크립트로만 일한다. 이 SKILL.md가 있는 폴더의 옆 폴더 `p3-engine`의 `scripts/p3`를 `sh`로 부른다(`sh <이 SKILL.md가 있는 폴더>/../p3-engine/scripts/p3 <명령>`, 아래에서 `p3 <명령>`이라고 줄여 쓴다). 사용자가 지금 일하는 폴더(작업 폴더 `.text2sql/`가 있는 폴더)에서 부르고, 명령 인자의 상대 경로는 그 폴더 기준이다. 명령 출력에 `t2s <명령>`이라고 나오면 `p3 <명령>`으로 읽는다. 작업 폴더(`.text2sql/`)가 없다고 나오면 `p3 init`을 돌릴지 한 줄로 묻는다. `p3`가 `no_uv`(uv 없음)나 `setup_failed`(처음 환경 만들기 실패)를 돌려주면 그 메시지를 그대로 안내하고 멈춘다. 스킬 이름은 `/이름`으로 적었다. Codex에서는 `$이름`, 플러그인으로 깔았으면 `/text2sql:이름`으로 부른다.

## 절차

1. 실행한다:

   p3 fetch bird

   이미 있고 해시가 맞는 파일은 건너뛰므로 여러 번 불러도 된다. 끝까지 가면 10줄 이내 요약과 라이선스 표기를 출력한다(zip 0.8GB, 모델 0.4GB 다운로드). 요약에는 공개 evidence 줄(`공개 evidence: 만듦(500문항, …)` 또는 `유지(…)`)이 있다. 공개 evidence 파일(`.text2sql/data/bird-minidev/hints.public.json`)은 공식 문제 파일에서 위치별 question_id와 evidence 글만 떼어 만들고 sha256을 대조한 것이며, evidence used run에서만 쓴다. 명령이 "작업 폴더가 없습니다. 먼저 init"으로 끝나면 `p3 init`을 돌릴지 한 줄로 묻고, 좋다고 하면 `p3 init` 뒤 다시 실행한다. 이미 BIRD를 받은 사람도 /getbird를 다시 부르면 이 파일이 생긴다(다른 파일은 해시만 대조하고 건너뛴다).

2. 종료 코드 0이면 출력된 요약을 그대로 보여 준다. 줄이거나 해석을 덧붙이지 않는다. 마지막에 "다음: /connect bird로 BIRD를 지금 데이터셋으로 정한 뒤 /runset(전체 실행) 또는 /text2sql(질문 하나). 값 색인·프로파일 준비(약 2분)는 /connect bird가 하거나 처음 부를 때 자동으로 합니다"를 알린다.

3. 종료 코드가 0이 아니면 `중단 N단계:` 줄과 그 아래 조치 줄을 그대로 보여 주고 멈춘다.
   - 네트워크 오류(`curl N`)면 한 번만 다시 부른다(.part에서 이어받는다). 두 번째도 실패하면 더 시도하지 않고 사용자에게 보고한다.
   - 해시 불일치면 다시 부르지 않는다. 원격 자료가 바뀌었거나 손상된 것이며, 다음 할 일은 사용자가 정한다.

## 지킬 것

- `.text2sql/gold/` 아래 파일, 받은 zip(`.text2sql/data/*/download/`), 공개 evidence 파일(`.text2sql/data/bird-minidev/hints.public.json`)을 열거나 내용을 출력하지 않는다.
- 사용자에게 명령 인자를 치라고 하지 않는다.
- 기본은 evidence not_used(퓨어 조건: 문항의 evidence와 사람이 쓴 설명을 모델에 주지 않는다)다. 사용자가 evidence(문항 조건)를 주라고 말했을 때만 used로 돈다.
- 기본은 semantic not_used다. 사용자가 지표 정의를 넣으라고 말했을 때만 semantic used로 돈다. evidence와 함께 쓰지 않는다.
- 지표 정의 파일(`.text2sql/data/*/semantic.approved.json`, `.text2sql/var/semantic-inbox/` 아래 파일)을 통째로 읽거나 내용을 출력하지 않는다. 명령이 낸 요약 줄만 보인다.
- 새 DB는 밖으로 나가는 것을 알리고 승인을 받은 뒤에만 등록한다. 민감한 DB에 쓰지 않는다.
- DB 파일을 직접 열거나 DB 클라이언트·다른 도구로 스키마를 읽지 않는다.
- 세션 모델이 SQL을 직접 쓰지 않는다. SQL은 `p3 ask`·`p3 run`이 만든다.
- 점수 라벨을 줄이지 않는다. 백엔드·evidence 방식·경로가 다른 점수끼리 우열을 말하지 않고, 원 논문·리더보드·스터디 E0 점수를 덧붙이지 않는다.
- 모델을 알 수 없으면 돌리지 않고 묻는다.
- 채점한 run은 이어 돌릴 수 없다. 같은 run을 두 번 시작하지 않는다.
- 커밋과 푸시는 사용자가 말할 때만 한다.
- 플러그인 폴더와 엔진 스킬 폴더에는 아무것도 쓰지 않고, 엔진 소스를 읽지 않는다. 자료와 기록은 `.text2sql/`에만 생긴다.
- run 폴더의 `records.jsonl`·`items/` 같은 큰 파일을 통째로 읽지 않는다.

## 하지 말 것

- `.text2sql/gold/` 아래 파일, `.text2sql/data/bird-minidev/download/` 아래 파일, `.part`·`.rejected-*` 파일을 열어 보거나 내용을 터미널에 출력하지 않는다(정답 SQL·evidence가 들어 있다).
- 공개 evidence 파일(`.text2sql/data/bird-minidev/hints.public.json`)의 내용을 터미널에 출력하지 않는다.
- 엔진 스킬 폴더의 파일(받는 곳과 해시를 고정한 설정)을 고치지 않는다. 해시를 건너뛰는 옵션·환경변수를 더하지 않는다. 해시가 다르면 멈추는 것이 맞는 동작이다.
- 값 색인·프로파일을 여기서 만들지 않는다. 그것은 /runset·/text2sql이 처음 돌 때, 또는 /connect bird가 한다.
