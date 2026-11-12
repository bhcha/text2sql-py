# text2sql-py 운영 규칙

이 저장소는 P3 text-to-SQL 파이프라인(11단계)을 Python으로 만든 하네스다. 사용법은 `README.md`, 출처는 `docs/provenance.md`에 있다. 아래 규칙은 이 저장소에서 일하는 모든 에이전트(Claude Code, Codex)와 사람이 따른다.

1. 코드는 Python 3.13이고 uv로 관리한다. 실행은 `uv run`, 의존은 `pyproject.toml`과 `uv.lock`으로만 바꾼다. 실행 의존은 numpy·onnxruntime·tokenizers 셋이다. 의존을 더하려면 사용자 확인을 받는다. 검사는 `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`이다. 예외: 공식 채점기 `src/t2s/eval/vendor/**`는 고치지 않는다.

2. 모듈 경계. `tests/test_import_rules.py`의 import 표를 따른다. `t2s.eval`을 import하는 것은 `cli/eval_cmds.py`뿐이고, `t2s.fetch`를 import하는 것은 `cli/fetch_cmd.py`뿐이다. 이 테스트를 고쳐서 통과시키지 않는다. 경계를 바꿔야 하면 사용자 확인을 받는다.

3. 정답 격리. 정답·evidence가 든 파일은 `gold/`에만 둔다. 정답 설정(`gold.toml`)과 채점 중간 파일(`gold/_work/`)도 `gold/`에 둔다. 생성 쪽(`datasource`, `dataset`, `prep`, `pipeline`, `llm`, `runner`)은 `gold/`를 읽거나 문자열로 가리키지 않는다. `ask`·`run`·`prepare`는 읽기 감시를 걸고 돈다. 모든 500문항 run에 `t2s leak-audit`을 돌려 0건을 확인한다.

4. 출처 기록. 다른 저장소 코드를 복사해 고친 파일은 머리말에 출처 저장소·커밋·원본 파일·줄·바꾼 것을 적고 `docs/provenance.md` 표에 한 줄을 더한다. CHESS는 Apache-2.0, DAMO-ConvAI는 MIT 고지를 유지한다. MAC-SQL은 코드도 문장도 복사하지 않고 동작만 다시 구현하며, 읽은 파일·줄을 `docs/provenance.md`에 적는다. MAC-SQL 원문 템플릿 파일을 실행 때 읽지 않는다.

5. 측정 규칙. 커밋한 깨끗한 작업 트리에서만 500문항을 잰다(라벨의 `(미커밋 코드)`를 대표로 쓰지 않는다). 측정 중 `src/` 아래를 바꾸지 않는다. `caffeinate -i`로 잠자기를 막는다. 채점한 run은 이어 돌릴 수 없다. 측정 앞뒤의 CLI 판(`codex --version`, `claude --version`)과 Python·SQLite 판을 run 기록에 남긴다.

6. 라벨과 문장. 모든 점수에 `BIRD Mini-Dev / SQLite / cc48ba16 / 500문제 / EX / evidence not_used / 로컬 측정 / <모델>·추론 <단계>·<백엔드> / 1회 실행 / 구현 Python <커밋>`을 붙인다. 새로 맞음·새로 틀림을 함께 적는다. 차이가 13 이하이거나 부호 검정 p가 0.05 이상이면 "차이 없음(변동 범위)"이라고 쓴다. 백엔드가 다른 점수끼리 우열을 말하지 않는다. 원 논문·리더보드 점수와 같은 줄에 두지 않는다.

7. 기록은 덮어쓰지 않는다. 기준선 파일(`baselines/`)은 덮어쓰지 않고 새 이름으로 쓴다. `docs/provenance.md`에는 줄·절을 더하기만 한다.

8. 모델 설정. 참조 프로필 `p3-ref`는 codex-cli·gpt-5.6-terra·추론 medium 고정이며 검증 측정은 이 프로필로만 한다. 추론 단계를 올리거나 다른 모델을 재는 것은 사용자가 정한다. 스킬의 기본은 지금 세션의 모델이고, 대화로 지정하면 그 모델을 쓴다. 모델을 알 수 없으면 돌리지 않고 묻는다. 기록에 `model_source`를 남긴다.

9. 퓨어 조건. evidence, 열 설명 CSV, 열 뜻 파일, BIRD 학습 세트, 손으로 쓴 규칙을 쓰지 않는다. DB에서 자동으로 꺼낸 것(CREATE 원문, 예시 행, 값 색인, 열 프로파일, 실행 결과)과 범용 모델만 쓴다. 키워드 프롬프트의 힌트 칸은 늘 빈 글이다. BIRD는 evidence not_used만 잰다.

10. 읽기 전용 DB. 모든 DB 열기는 `mode=ro&immutable=1`이고, authorizer로 읽기 밖의 동작을 거절하며, 한 번에 한 문장만 받고, 실행 전후 DB sha256을 비교한다. 사용자 DB는 다른 프로그램이 그 DB에 쓰고 있지 않을 때만 쓴다(`immutable=1`은 그때만 안전하다).

11. 밖으로 나가는 것. 질문, 스키마(CREATE 원문과 예시 3행), 질문과 비슷한 값, 열 프로파일(범주형 열은 값 전부), 고치기 때의 SQLite 오류 글이 모델 제공자 서버(OpenAI 또는 Anthropic)로 나간다. 정답 SQL은 나가지 않는다. DB를 등록할 때와 질문할 때 이것을 알린다. 민감한 DB에 쓰지 않는다.

12. 문서 규칙. 한국어, 평범한 말로 쓴다. 섹션 기호나 학술식 절 표기를 쓰지 않는다('1단계', '4절', 'AGENTS 규칙 4'처럼 쓴다). 확인한 사실과 추정을 구분하고 미확인은 미확인으로 적는다. 문서와 주석에 로컬 절대 경로를 쓰지 않는다. 사용자가 지시한 것만 하고 그 밖은 확인을 받는다. 커밋과 푸시는 사용자가 말할 때만 한다.

13. 스킬 사용 규칙. 사용자에게 명령 인자를 요구하지 않는다. `/runset`은 start → status → report만 부르고 run 파일 전체를 읽지 않는다. `/text2sql`은 세션 모델이 직접 SQL을 쓰지 않고 `t2s ask`를 돌린다. `/getbird`는 원격에서만 받고 해시가 다르면 멈춘다. `/connect`는 새 DB를 등록하기 전에 밖으로 나가는 것을 알리고 승인을 받는다. 어떤 스킬도 `gold/` 아래 파일과 받은 zip의 내용을 터미널에 출력하지 않는다.
