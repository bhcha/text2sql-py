# 밖으로 나가는 것

- 모델 호출은 이 기기의 `codex` 또는 `claude` 명령으로 한다. 그 호출에 실려 모델 제공자 서버(OpenAI 또는 Anthropic)로 나가는 것: 질문, 스키마(CREATE 원문과 예시 3행), 질문과 비슷한 값, 열 프로파일(범주형 열은 값 전부), 고치기 때의 SQLite 오류 글.
- evidence used run에서는 문항의 evidence 글(BIRD 공식 evidence 칸, 또는 Olist 문항 조건을 '; '로 이은 글)도 나간다.
- 단일 호출 경로는 질문과 CREATE 원문만 나간다(evidence used면 evidence 글도).
- 정답 SQL은 나가지 않는다.
- DB를 등록할 때(`p3 register`가 고지를 내고 종료 코드 3으로 승인을 기다린다)와 질문할 때 이것을 알린다. 민감한 DB에는 쓰지 않는다.
- 가짜 백엔드(`--backend fake`)는 아무것도 내보내지 않는다.
- 받기(`p3 fetch`)는 BIRD 공식 자료, 임베딩 모델, 스터디 저장소(gh)에서 내려받기만 한다.
