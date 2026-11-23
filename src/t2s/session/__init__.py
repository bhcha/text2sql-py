"""세션 폴더의 이름 규칙과 색인(`index.jsonl`). 표준 라이브러리만 쓰고 다른 t2s 패키지를 import하지 않는다.

세션 하나는 실행 기록 폴더 하나다(`run`·`route-start`의 run, `ask` 한 번, 지표 길의 세션). `cli`와 `runner`만 이 패키지를
import한다(AGENTS 규칙 2).
"""
