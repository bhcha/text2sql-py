# 출처 기록

이 문서는 다른 저장소에서 가져온 코드와 착안을 파일·단계별로 적는다. 새 파일을 복사해 고치거나 다른 저장소의 동작을 다시 구현할 때마다 이 문서에 줄을 더한다(덮어쓰지 않는다). 고지 전문은 `LICENSES/NOTICE`에 있다.

## 1. 원칙

- **복사 후 수정**: 원본 코드를 가져와 고친 것이다. 고친 파일의 머리말에 출처 저장소, 커밋, 원본 파일, 줄, 바꾼 것을 적는다. 원본 라이선스 고지를 유지한다.
- **재구현**: 원본의 동작만 읽고 코드를 새로 쓴 것이다. 원본 코드와 문장을 옮기지 않는다. 읽은 파일과 줄을 이 문서에 적는다.
- **자체**: 오픈소스 출처가 없는 코드다. TS 하네스 코드를 Python으로 옮긴 것도 여기에 든다(TS 하네스 text2sql-harness는 같은 작성자의 저장소다).

## 2. 출처 저장소

| 저장소 | 주소 | 받아 둔 커밋 | 라이선스 | 비고 |
|---|---|---|---|---|
| CHESS | https://github.com/ShayanTalaei/CHESS | `3d6e835f858d26885d21d4bc0215aeecf855efbe` | Apache-2.0 (`LICENSES/Apache-2.0-CHESS.txt`) | NOTICE 파일 없음 |
| DAMO-ConvAI | https://github.com/AlibabaResearch/DAMO-ConvAI | `faf754671f5b850e1b4d1ff870db37a6a2480df6` | MIT, Copyright (c) 2022 Alibaba Research (`LICENSES/MIT-DAMO-ConvAI.txt`) | 아래 5절 둘째 문구 |
| MAC-SQL | https://github.com/wbbeyourself/MAC-SQL | `31a9df5e0d520be4769be57a4b9022e5e34a14f4` | 표기 없음 | 위 커밋의 저장소 내용만 보았다. 그 뒤 커밋에 라이선스 표기가 생겼는지는 미확인 |

## 3. 단계별 출처 표

| P3 단계 | 원본 파일·함수·줄 | 새 모듈 | 방식 | 라이선스 처리 |
|---|---|---|---|---|
| ③ 준비: 값 색인(값 고르기) | CHESS `src/database_utils/db_values/preprocess.py` `_get_unique_values` 10-73행 | `src/t2s/prep/value_index.py` | 복사 후 수정. SQL 실행을 `DataSource`로 옮김, 질의 시간 제한(480초) 없앰, 이름을 늘 백틱으로 감쌈. MinHash LSH(75-153행)는 가져오지 않음 | Apache-2.0. 파일 머리말에 출처·커밋·바꾼 것 |
| ③ 준비: 3-gram 역색인 | (오픈소스 없음) TS `engines/pure-values/pack/values.ts` `buildShingleIndex`·`jaccardTopN`, `index.ts` `serializeShingles` | `src/t2s/prep/value_index.py` | 자체(TS를 Python으로 옮김). 조각 정의는 CHESS `_create_minhash`(75-90행)의 `string[i:i+3]`과 같음 | 자체 |
| ⑤ 준비: 열 프로파일 | CHESS `src/database_utils/schema_generator.py` `_load_schema_into_cache` 103-104행(범주형 판정 질의 한 줄) | `src/t2s/prep/profile.py`, `src/t2s/datasource/sqlite.py` `column_profile` | 범주형 판정 질의만 복사. 나머지(행 수·NULL 수·형식 분포·값 전부·최소 최대·길이)는 TS `engines/pure-profile/pack/profile.ts`를 다시 구현 | Apache-2.0(한 줄 규칙), 나머지 자체 |
| ① 스키마 글 | DAMO-ConvAI `bird/llm/src/gpt_request.py` `nice_look_table` 44-58행, `generate_schema_prompt` 60-97행 | `src/t2s/pipeline/schema_text.py` | 복사 후 수정. `sqlite3.connect`를 `DataSource`(`tables`·`ddl`·`sample_rows`)로, 테이블 블록 목록과 예시 행 수를 함께 돌려줌, 읽기 전용 연결. 값 표기는 원본처럼 `str()` | MIT 고지 유지. 파일 머리말에 출처·커밋·바꾼 것 |
| ② 키워드 추출 | CHESS `templates/template_extract_keywords.txt`(전체), `src/workflow/agents/information_retriever/tool_kit/extract_keywords.py` `_run` 21-36행, `src/llm/parsers.py` `PythonListOutputParser.parse` 18-32행 | `src/t2s/pipeline/keywords.py`, `src/t2s/pipeline/templates/extract_keywords.txt` | 템플릿은 바이트 그대로 복사(sha256 `4ffe8fdcb1d9c8b07ab27c15c2fd53195406b87541d3b43ee7fd0889c8eeba17`). 파서는 복사 후 수정: `eval` 대신 `ast`, langchain 제거, 못 읽으면 같은 프롬프트로 최대 3번(TS 규칙 1.0.0). `{HINT}`는 늘 빈 글 | Apache-2.0. 템플릿 파일 안에는 머리말을 넣을 수 없어 `keywords.py` 머리말과 이 표에 적음 |
| ③ 값 검색 | CHESS `retrieve_entity.py` `_column_value` 75-90행, `_get_similar_entities` 189-220행, `_get_to_search_values` 222-250행, `_get_similar_entities_via_LSH` 252-266행, `_get_similar_entities_via_edit_distance` 268-275행, `_get_similar_entities_via_embedding` 277-306행, 문턱 34-36행. `db_values/search.py` `query_lsh` 48-78행(묶는 순서) | `src/t2s/pipeline/value_search.py` | 복사 후 수정. LSH를 정확 자카드 상위 10으로, OpenAI 임베딩을 로컬 all-mpnet-base-v2로(글 하나씩, 캐시), 내적은 float64, 문턱과의 거리 기록을 더함. 문턱 0.3·0.6·0.9배와 순서는 그대로 | Apache-2.0 |
| ④ 값 줄, ⑤ 프로파일 줄 | (오픈소스 없음) TS `engines/pure-fix-h/pack/attach.ts`, `profile-line.ts` | `src/t2s/pipeline/attach.py` | 자체(TS 꼴을 그대로 옮김) | 자체 |
| ⑥⑦ 주석·결합 | DAMO-ConvAI `gpt_request.py` `generate_comment_prompt` 99-111행, `cot_wizard` 113-116행, `generate_combined_prompts_one` 138-147행 | `src/t2s/pipeline/prompt.py` | 복사 후 수정. 방언 이름을 인자로(`SQLite`면 원본과 같은 글), 스키마 글을 밖에서 받음. 철자 `understading`은 원본 그대로 | MIT 고지 유지 |
| ⑧ 모델 호출 계약·가짜 백엔드 | (오픈소스 없음) TS `engines/packkit/llm/types.ts`, `fake.ts` | `src/t2s/llm/base.py`, `src/t2s/llm/fake.py` | 자체. `call`이 호출 이름·차례를 받음, `prompt.txt`를 남김 | 자체 |
| ⑨ SQL 꺼내기 | 착안: DAMO-ConvAI `gpt_request.py` 184행(stop 목록)·189-192행, `post_process_cot.py` `fetch_sql` 13-14행. 규칙: TS `engines/pure-fix-h/pack/extract.ts` 1.2.0 | `src/t2s/pipeline/extract.py` | 재구현(코드 복사 없음). TS 규칙 다섯 갈래를 그대로 옮기고 JavaScript 정규식의 뜻(`\s`·`\w`·`\b`·`trim`)을 풀어 씀 | 착안 출처로 DAMO 기록 |
| ⑩ 실행·판정 | MAC-SQL `core/agents.py` `Refiner._execute_sql` 671-698행, `_is_need_refine` 700-719행, `talk` 756-779행. TS `fix.ts` `decide` | `src/t2s/pipeline/judge.py`(실행은 `datasource/sqlite.py` `execute`) | 재구현(코드·문장 복사 없음, 5절 원칙). 0행·NULL 안내 문구는 새로 씀 | 코드 복사 없음. 읽은 곳은 5절 표 |
| ⑪ 고치기 | MAC-SQL `core/agents.py` `Refiner.talk` 740-786행, `_refine` 721-738행, `Selector._get_db_desc_str` 511-523행, `core/const.py` 1행·565-595행(칸 구성만), `core/chat_manager.py` 46-62행, `core/utils.py` 346-356행·415-418행. TS `fix.ts`·`run.ts` 반복 | `src/t2s/pipeline/refine.py`, `src/t2s/pipeline/templates/refine.txt` | 재구현. 차례 3번, 마지막 ```` ```sql ```` 블록, 블록이 없으면 이전 SQL로 멈춤(TS 어댑터 차이 유지), `add_prefix` 동작 유지. 템플릿은 일곱 칸의 구성과 뜻만 따르고 문장은 새로 씀(첫 판 sha256 `27581623e0d89e14f9998dc66df64c06cedf992e5fa791ec75eae09473e366d4`) | 코드·문장 복사 없음. 읽은 곳은 5절 표 |
| ⑧ 모델 호출(Codex·Claude) | (오픈소스 없음) TS `engines/packkit/llm/codex.ts`, `claude.ts`, `types.ts` | `src/t2s/llm/codex_cli.py`, `claude_cli.py`, `_proc.py` | 자체(TS를 옮김). 인자 배열은 TS와 같음. 작업 폴더를 저장소 밖 임시 폴더로, 기록 파일 이름을 `cli-events.jsonl`·`cli-stderr.txt`로 통일 | 자체 |
| 모델 고르기 | (오픈소스 없음) TS `harness/lib/model-select.ts` | `src/t2s/llm/select.py` | 자체(TS를 옮김). 프로필은 `harness.toml`의 표에서 받음 | 자체 |
| 질문 하나 | (오픈소스 없음) TS `harness/ask.ts` | `src/t2s/cli/ask_cmd.py` | 자체. 출력 줄 꼴을 옮김. 팩 프로세스 대신 같은 프로세스에서 `pipeline.run.answer`를 부름 | 자체 |
| 채점 | BIRD mini_dev `evaluation/evaluation_ex.py`·`evaluation_utils.py`(커밋 `abd11b6db92a1c9f809b32f7564c7c71b34d67f0`) | `src/t2s/eval/vendor/bird-minidev-abd11b6/` | 고치지 않고 복사(sha256 대조: `da1bbcd4…cf89`, `f6943d24…08bf`). ruff 검사에서 뺌 | CC BY-SA 4.0(mini_dev README 표기, `LICENSES/NOTICE` 3번) |
| 채점 연결 | (오픈소스 없음) TS `benchmarks/vendor-bridge/ex_worker.py`, `benchmarks/benchkit/submit.ts`·`score.ts` | `src/t2s/eval/ex_worker.py`, `src/t2s/eval/bird.py`, `src/t2s/eval/gold.py` | 자체(TS를 옮김). 공식 함수를 부르기만 하고 판정 논리는 없음 | 자체 |
| 일괄 실행 | (오픈소스 없음) TS `benchmarks/benchkit/run.ts`, `harness/allbird.ts` | `src/t2s/runner/batch.py`, `src/t2s/runner/records.py`, `src/t2s/cli/run_cmds.py` | 자체(TS를 옮김). 팩 프로세스 대신 스레드, 한도 연속 5회 멈춤은 그대로, 호출 실패 연속 20회 멈춤과 분리 전 확인 호출을 더함 | 자체 |
| 보고·누설 검사 | (오픈소스 없음) TS `benchmarks/benchkit/report.ts`, `harness/lib/sentence.ts`, `harness/leak-audit.ts` | `src/t2s/eval/report.py`, `src/t2s/eval/leak_audit.py` | 자체(TS를 옮김). 부호 검정·문장 규칙은 같은 식 | 자체 |

## 4. Python판에서 달라진 동작

| 무엇 | TS | Python판 | 까닭 |
|---|---|---|---|
| 값 목록 질의의 이름 감싸기 | 이름 안의 백틱·작은따옴표를 처리하지 않아 그런 이름이면 질의가 실패하고 그 열이 빠진다 | 이름을 늘 백틱으로 감싸고 안의 백틱은 두 번 쓴다 | 바른 질의. BIRD 11개 DB에는 그런 이름이 없어 결과가 같다(개발 중 TS 산출물과 대조해 확인) |
| 값 목록 크기 질의 실패 | 실패한 열을 조용히 뺀다 | 같게 빼되 실패 수를 manifest `counts.value_query_errors`에 센다 | 기록용 |
| 읽기 밖 SQL(쓰기·임시 테이블) | SQLite가 `attempt to write a readonly database`로 거절, 임시 테이블은 성공 | authorizer가 둘 다 `not authorized`(일부는 `authorization denied`)로 거절, `ExecResult.denied=True` | 읽기 밖의 동작을 모두 막으려고 authorizer를 더했다 |
| `DISTINCT` 값 순서, `GROUP BY 1` 줄 순서 | Node 22의 SQLite 3.51.2가 정함 | Python 3.13.7의 SQLite 3.50.4가 정함 | 두 판에서 같았다(확인). 다른 판은 미확인이라 자산 manifest에 `sqlite_version`을 남긴다 |
| ② 키워드 응답 읽기 | `parsePyLiteral`(Python 리터럴 흉내)로 읽고, 사전의 겹친 키를 모두 남긴다 | `ast`로 리터럴만 읽는다. 집합은 적힌 순서로 겹친 것만 줄이고, 사전은 `literal_eval` 규칙(겹친 키는 하나) | 모델 응답을 실행하지 않게. TS 대표 run 응답 500개는 모두 글만 든 목록이라 차이가 나지 않았다(개발 중 확인) |
| ⑨ 줄 끝 글자 | JavaScript m 깃발의 `^`·`$`는 `\r`·U+2028·U+2029도 줄 끝으로 본다 | Python `re.M`은 `\n`만 본다 | 흉내 내지 않고 기록만 한다. TS 대표 run 응답에는 이 글자가 없었다 |
| ⑩ 묶지 않은 자리표(`:name`) | node:sqlite가 NULL로 묶어 실행한다(위치 82: 0행) | `sqlite3.ProgrammingError`(`Incorrect number of bindings supplied…`) | 드라이버 동작 차이로 받아들인다. MAC-SQL 원본도 Python `sqlite3`이라 Python판이 원본 쪽이다. 고칠지 말지와 최종 SQL은 TS와 같다 |
| ⑩ 빈 결과의 열 수 | 0(첫 행에서 열을 센다) | 실제 열 수(`cursor.description`) | 판정에 영향 없음 |
| ⑩ 시간 초과의 예외 종류 글 | 빈 글 | `<class 'sqlite3.OperationalError'>` | 프롬프트에 들어가지 않는다(시간 초과면 고치지 않는다) |
| ⑩ 실행 방식 | 별도 Node 프로세스를 120초에 끝냄 | 같은 프로세스에서 진행 처리기로 120초에 끊음 | 개발 중 시간 제한 동작 확인. 위치 372는 양쪽 다 시간 초과 |
| ⑪ 고치기 템플릿, 0행·NULL 문구 | MAC 원문 템플릿과 원문 문구 | 새로 쓴 템플릿(`templates/refine.txt`)과 문구(`judge.py`) | 5절 원칙. 고치기 프롬프트는 TS와 같지 않다(글자 수 평균 +264) |
| ① 예시 행 질의 | 이름을 order·by·group만 감싸 실행(따옴표가 필요한 이름이면 SQL 오류) | `sample_rows`가 이름을 늘 감싸 실행. 프롬프트에 적는 질의 글은 원본 그대로 | BIRD 11개 DB에서는 글이 같다(생성 프롬프트 sha256 500/500) |
| 모델 CLI 작업 폴더 | run 폴더 안 `codex-work`·`claude-work` | 저장소 밖 임시 폴더(`t2s-llm-` 접두어, 호출 뒤 지움) | 위쪽 폴더의 `CLAUDE.md`·`AGENTS.md`·정답 폴더와 떨어뜨려 "빈 작업 폴더"를 글자 그대로 참으로 |
| 모델 CLI 기록 이름 | `codex-events.jsonl`·`claude-events.jsonl`, CLI 판 칸 `codex_version` | 두 백엔드 모두 `cli-events.jsonl`·`cli-stderr.txt`, 칸 `cli_version` | 백엔드와 상관없이 같은 이름으로 읽게 |
| 채점 중간 파일 자리 | 난도 파일(`diff.full.json`·`diff.subset.json`)과 소표본 정답(`gold.subset.sql`)을 run 폴더에 씀 | `gold/_work/<run_id>/`에 씀(`diff.jsonl`, `gold.subset.sql`, `ex.raw.jsonl`). run 폴더에는 예측·문항별 0·1·요약만 | 정답 글자가 run 폴더에 생기지 않게 |
| 소표본 채점 | 공식 채점기를 늘 실행(난도가 빠지면 0으로 나누기 오류) | 앞 N문항에 없는 난도가 있으면 공식 채점기를 건너뛰고 문항별 판정만 냄(교차 대조 `null`) | 오류로 멈추지 않게 |
| 생성·채점의 프로세스 나눔 | 문항마다 팩 프로세스, 채점은 benchkit 하위 명령 | 생성은 한 프로세스의 스레드 풀(기본 4), 채점은 별도 프로세스(`t2s score`). 분리된 생성 프로세스가 끝나면 `score`·`report`를 하위 프로세스로 이어 부름(`--no-score`로 끔) | 임베딩 모델을 한 번만 싣고, 읽기 감시가 걸린 생성 프로세스가 정답을 읽지 않게 |
| 누설 검사 범위 | 지금 시도의 프롬프트만 | 이어 돌 때 옮겨 둔 `<위치>.attempt-<n>` 폴더의 프롬프트도 봄 | 이전 시도에 섞인 누설도 놓치지 않게 |
| 실패 유형 분류 | `analyze.ts`가 실패 유형을 나눔 | 없음 | 이 저장소 범위 밖 |
| 최종 SQL의 실행 상태 | packkit이 최종 SQL을 늘 한 번 더 실행 | 마지막 차례에서 실행된 SQL이면 그 결과를 쓰고(`source: round`), 셋째로 고친 SQL·`error` 글로 건너뛴 SQL만 한 번 실행(`source: extra`) | 같은 SQL을 두 번 돌리지 않게 |

## 5. 두 가지 고정 문구

### MAC-SQL 재구현 원칙

MAC-SQL 코드는 한 줄도 복사하지 않는다. 고치기 프롬프트와 0행·NULL 안내 문구는 같은 칸 구성으로 새로 쓴다. 원문 템플릿 파일을 실행 때 읽지 않는다. 읽은 파일과 줄을 아래에 적는다(줄 번호는 2절 표의 커밋 기준).

| 파일 | 함수·줄 | 읽은 까닭 |
|---|---|---|
| `core/const.py` | 1행 `MAX_ROUND = 3`, `refiner_template` 565-595행 | 차례 수, 고치기 템플릿의 칸 구성 |
| `core/chat_manager.py` | `_chat_single_round` 46-50행, `start` 52-62행 | 차례 흐름 |
| `core/agents.py` | `Refiner._execute_sql` 671-698행 | 120초, `fetchall`, 앞 5행, 오류 글·예외 종류 |
| `core/agents.py` | `Refiner._is_need_refine` 700-719행 | 오류 / 0행 / 앞 5행 None이면 고침 |
| `core/agents.py` | `Refiner._refine` 721-738행, `Refiner.talk` 740-786행 | 템플릿 채우기, `'error'` 글이면 건너뜀, 시간 초과면 멈춤 |
| `core/agents.py` | `Selector._get_db_desc_str` 511-523행 | 외래키 글 꼴 |
| `core/utils.py` | `parse_sql_from_string` 346-356행, `add_prefix` 415-418행 | 마지막 ```` ```sql ```` 블록, `SELECT` 붙이기 |

### DAMO-ConvAI 문구 인용

DAMO-ConvAI 저장소의 `LICENSE`는 MIT다. 그런데 `bird/README.md`에는 아래 두 줄이 있다(2절 표의 커밋 기준 13행과 20행, 그대로 인용).

13행:

> [![License](https://img.shields.io/badge/License-CC%20BY%20SA%204.0-orange.svg)](https://creativecommons.org/licenses/by-sa/4.0/deed.en)

20행:

> **License Notation**: Due to large volume of requests, now we change the license to <code>CC BY-SA 4.0</code>.

이 문구가 BIRD 데이터셋에만 해당하는지 `bird/` 아래 코드까지 해당하는지는 그 글만으로 가릴 수 없다(미확인). 이 저장소는 저장소 `LICENSE`(MIT)를 따라 복사 후 수정하고, 이 문구를 여기에 남겨 둔다.
