---
name: p3-engine
description: P3 text-to-SQL 엔진(코드, 기본 설정, 기준선)을 담은 스킬이다. 사용자가 직접 부르지 않고, 다른 스킬(connect, getbird, getolist, text2sql, runset, newmodel, sessions)이 이 폴더의 scripts/p3 를 부른다.
when_to_use: 직접 부르지 않는다. 다른 스킬의 글이 이 폴더의 scripts/p3 를 가리킬 때만 그 스크립트를 쓴다.
disable-model-invocation: true
---

# p3-engine — 엔진을 부르는 법

## 부르는 한 줄

이 폴더의 `scripts/p3`를 `sh`로 부른다: `sh <이 폴더>/scripts/p3 <명령> [인자]`. 다른 스킬의 글은 이것을 `p3 <명령>`이라고 줄여 쓴다. 명령 출력에 `t2s <명령>`이라고 나오면 `p3 <명령>`으로 읽는다.

처음 한 번 `p3 setup`이 전용 Python 환경을 만든다(uv가 필요하고 인터넷이 든다). 환경은 플러그인 폴더 밖(`T2S_HOME`, 기본은 사용자 데이터 폴더 아래 `text2sql-plugin/`)에 생긴다. Codex처럼 쓰기를 막는 곳에서는 보통 터미널에서 `p3 setup`을 먼저 한 번 돌린다.

## 작업 폴더

- 작업 루트는 사용자가 일하는 폴더의 `.text2sql/`이다. `T2S_ROOT`가 있으면 그것을, 없으면 부른 폴더에서 위로 올라가며 가장 가까운 `.text2sql/`를 쓴다.
- 없으면 "작업 폴더가 없습니다. 먼저 init"이 나오고 종료 코드 2다. 그때 `p3 init`을 돌릴지 사용자에게 한 줄로 묻는다. `init`은 부른 폴더에 `.text2sql/`를 만들고 기본 설정(`harness.toml`)과 기준선(`baselines/`)을 복사한다. 있는 파일은 덮어쓰지 않는다.
- 받은 자료(`data/`), 정답(`gold/`), 실행 기록(세션 폴더 `sessions/`), 상태(`var/`)는 모두 `.text2sql/` 아래에 생긴다. 설정을 바꾸는 것(`route-apply`)도 `.text2sql/harness.toml`에 쓴다.

## 하지 말 것

- 이 폴더의 소스(`src/`)와 `assets/`를 읽지 않는다. 엔진이 하는 일은 명령 출력으로만 안다.
- 이 폴더와 플러그인 폴더에 아무것도 쓰지 않는다. 엔진의 `t2s`를 PATH에 올리지 않는다.
- `.text2sql/gold/` 아래 파일, 받은 zip(`.text2sql/data/*/download/`), 공개 evidence 파일(`.text2sql/data/bird-minidev/hints.public.json`)을 열거나 내용을 출력하지 않는다.
- 해시 대조를 건너뛰는 옵션이나 환경변수를 더하지 않는다.

## 명령 묶음

- 받기: `p3 fetch bird`, `p3 fetch model`, `p3 fetch olist`
- 데이터셋: `p3 datasets`, `p3 use <이름>`, `p3 register <이름> --sqlite <경로>`(고지 뒤 `--yes`), `p3 prepare [<이름>]`
- 질문 하나: `p3 ask [--source <DB>] <모델 인자> "<질문>"`
- 묶음 실행: `p3 run`, `p3 status`, `p3 stop`, `p3 resume`
- 채점과 보고: `p3 score`, `p3 report`, `p3 leak-audit`
- 경로 판정: `p3 route-start`, `p3 route-status`, `p3 route-judge`, `p3 route-apply`
- 세션: `p3 sessions`(최근 세션 20개), `p3 session open --kind <종류>`·`p3 session note`·`p3 session close`(지표 길의 세션 폴더)
- 작업 폴더와 환경: `p3 init`, `p3 init --refresh`, `p3 setup`, `p3 verify`(엔진 폴더의 파일과 깔린 엔진 패키지가 엔진 표식 `ENGINE.json`과 같은지)

## 더 읽을 것 (필요할 때만)

- `references/commands.md`: 명령 표 전체와 종료 코드 0~5의 뜻.
- `references/labels.md`: 점수 라벨과 견주는 규칙.
- `references/session-layout.md`: `.text2sql/` 아래에 무엇이 놓이는지와 세션 이름 규칙.
- `references/egress.md`: 모델 제공자 서버로 무엇이 나가는지.
