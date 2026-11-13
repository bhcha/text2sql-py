"""명령 fetch. t2s.fetch를 import하는 유일한 곳이다. 정답 파일을 옮겨야 하므로 읽기 감시를 걸지 않는다."""

from __future__ import annotations

import sys

from t2s.dataset.config import ConfigError, find_root
from t2s.fetch.bird import fetch_bird, fetch_model_only
from t2s.fetch.olist import fetch_olist
from t2s.fetch.steps import FetchStopped

EXIT_OK, EXIT_FAIL = 0, 1


def run(args) -> int:
    try:
        root = find_root()
    except ConfigError as e:
        print(f"오류: {e}", file=sys.stderr)
        return EXIT_FAIL
    try:
        lines = {"bird": fetch_bird, "model": fetch_model_only, "olist": fetch_olist}[args.what](root)
    except FetchStopped as e:
        for line in e.lines():
            print(line, file=sys.stderr)
        return EXIT_FAIL
    except OSError as e:
        # 파일 오류의 글에는 경로만 든다
        print(f"중단: 파일 오류 {type(e).__name__}: {e.filename or ''}", file=sys.stderr)
        return EXIT_FAIL
    except Exception as e:  # noqa: BLE001 — 내용이 섞일 수 있으므로 종류만 알린다
        print(f"중단: 예상 밖 오류 {type(e).__name__} (내용은 출력하지 않음)", file=sys.stderr)
        return EXIT_FAIL
    for line in lines:
        print(line)
    return EXIT_OK
