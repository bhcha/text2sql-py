"""모델 라우팅 1단계: 기존 run 기록을 다시 조합하는 오프라인 시뮬레이션(새 모델 호출 없음).

문항별 EX와 정답 SQL 실행 결과를 다루므로 평가 구역(t2s.eval)에 둔다(AGENTS 규칙 3). DB는 읽기 전용으로만 연다(AGENTS 규칙 10).
실행: uv run python -m t2s.eval.routing
"""
