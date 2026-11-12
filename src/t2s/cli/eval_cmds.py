"""명령 score·report·leak-audit. t2s.eval을 import하는 유일한 파일이다.

이 프로세스는 읽기 감시를 걸지 않는다(정답을 읽어야 한다). 정답 폴더·파일 이름은 여기 쓰지 않고 eval 안의 함수가
설정 루트에서 만든다. 종료 코드: 0 성공, 1 실패(대조 불일치·DB 바뀜·누설 포함), 2 쓰임새, 4 정답 없음(채점 생략).
"""

from __future__ import annotations

import sys

from t2s.dataset.config import ConfigError, find_root, load_config
from t2s.dataset.state import StateError, current_dataset, load_state
from t2s.eval import bird, leak_audit, report
from t2s.eval import gold as G
from t2s.runner import records as R

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NO_GOLD = 0, 1, 2, 4
NO_GOLD_LINE = "정답 없음: 채점 생략"


def out(line: str = "") -> None:
    print(line, flush=True)


def err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _setup():
    try:
        root = find_root()
        state = load_state(root)
        config = load_config(root, state)
    except (ConfigError, StateError) as e:
        err(f"오류: {e}")
        return None
    return root, state, config


def run(args) -> int:
    setup = _setup()
    if setup is None:
        return EXIT_FAIL
    _, state, config = setup
    handler = {"score": cmd_score, "report": cmd_report, "leak-audit": cmd_leak}[args.command]
    try:
        return handler(args, state, config)
    except G.NoGold:
        out(NO_GOLD_LINE)
        return EXIT_NO_GOLD
    except report.NotReady as e:
        out(str(e))
        return EXIT_FAIL
    except (G.GoldError, bird.ScoreError, report.ReportError) as e:
        err(f"오류: {e}")
        return EXIT_FAIL


def _run_id(args, config) -> str | None:
    return getattr(args, "run", None) or R.latest_run(config.runs_dir)


def cmd_score(args, state, config) -> int:
    if args.predictions:
        if not args.run_id:
            err("예측 파일로 채점하려면 --run-id <이름>이 필요합니다")
            return EXIT_USAGE
        dataset, _ = current_dataset(config, state)
        if not dataset or dataset not in config.datasets:
            err("데이터셋을 정하지 않았습니다(/connect)")
            return EXIT_USAGE
        G.load_gold(config.root, dataset)  # 정답이 없으면 run 폴더를 만들기 전에 끝낸다
        bird.import_predictions(config, dataset, args.predictions, args.run_id)
        run_id = args.run_id
    else:
        run_id = _run_id(args, config)
        if run_id is None:
            err("채점할 run이 없습니다")
            return EXIT_USAGE
    summary = bird.score(
        config,
        run_id,
        num_cpus=args.num_cpus,
        meta_time_out=args.meta_time_out,
        leak=lambda rid: leak_audit.short(leak_audit.audit(config, rid)),
    )
    out(bird.score_line(summary))
    return EXIT_FAIL if bird.score_failed(summary) else EXIT_OK


def cmd_report(args, state, config) -> int:
    run_id = _run_id(args, config)
    if run_id is None:
        out("시작한 run이 없습니다")
        return EXIT_FAIL
    try:
        lines, code = report.report(config, run_id, args.against)
    except G.NoGold:
        for line in report.no_gold_summary(config, run_id):
            out(line)
        return EXIT_OK
    for line in lines:
        out(line)
    return code


def cmd_leak(args, state, config) -> int:
    run_id = _run_id(args, config)
    if run_id is None:
        err("검사할 run이 없습니다")
        return EXIT_USAGE
    result = leak_audit.audit(config, run_id)
    out(leak_audit.leak_summary(result))
    return EXIT_FAIL if leak_audit.leaked(result) else EXIT_OK
