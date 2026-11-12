"""명령 `t2s`의 시작점. 하위 명령 모듈은 고른 명령에 따라 그때 불러온다.

그래서 `datasets`·`use`·`register`·`prepare`가 도는 프로세스에는 받기 코드(t2s.fetch)가 올라오지 않는다.
종료 코드: 0 성공, 1 실패, 2 쓰임새 오류(모델을 알 수 없음 포함), 3 승인 필요, 4 정답 없음(채점 생략), 5 준비 필요.
"""

from __future__ import annotations

import argparse
import sys

from t2s import __version__

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_NEEDS_APPROVAL = 3
EXIT_NO_GOLD = 4
EXIT_NEEDS_PREP = 5
# t2s.llm.select.BACKENDS와 같다(채점 프로세스가 llm을 불러오지 않게 여기 따로 둔다. test_select가 대조한다)
BACKENDS = ("codex-cli", "claude-code", "fake")


def add_model_arguments(parser) -> None:
    """모델 인자(스킬과 명령 사이의 내부 계약). 고르는 규칙은 t2s.llm.select.select_from_args."""
    g = parser.add_argument_group("모델(스킬이 넘기는 내부 인자)")
    g.add_argument("--profile", metavar="이름")
    g.add_argument("--backend", choices=BACKENDS)
    g.add_argument("--model", metavar="이름")
    g.add_argument("--effort", metavar="단계")
    g.add_argument("--model-source", dest="model_source", metavar="출처")
    g.add_argument("--effort-source", dest="effort_source", metavar="출처")
    g.add_argument("--session", choices=("claude", "codex"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="t2s", description="P3 text-to-SQL 하네스")
    parser.add_argument("--version", action="version", version=f"t2s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<명령>")

    sub.add_parser("datasets", help="데이터셋 목록, 지금 바라보는 것, 준비 상태")

    p = sub.add_parser("use", help="바라볼 데이터셋을 바꾼다")
    p.add_argument("name")

    p = sub.add_parser("register", help="SQLite 파일을 데이터셋으로 등록한다(var/state.json)")
    p.add_argument("name")
    p.add_argument("--sqlite", metavar="경로")
    p.add_argument("--yes", action="store_true", help="고지를 읽고 승인함")
    p.add_argument("--remove", action="store_true", help="등록 기록과 자산을 지운다")

    p = sub.add_parser("prepare", help="값 색인·프로파일을 만든다")
    p.add_argument("dataset", nargs="?")
    p.add_argument("--source", action="append", default=[], metavar="이름")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("ask", help="지금 데이터셋에 질문 하나")
    p.add_argument("--source", metavar="이름", help="소스가 여럿이면 고른다(BIRD는 DB 이름)")
    add_model_arguments(p)
    p.add_argument("question", metavar="질문")

    p = sub.add_parser("run", help="지금 데이터셋의 문항을 한꺼번에 돌린다(분리 실행)")
    p.add_argument("--limit", type=int, metavar="N", help="앞 N문항만")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--run-id", dest="run_id", metavar="이름")
    p.add_argument("--questions", metavar="파일", help="공개 문항 꼴 JSON 또는 질문이 한 줄에 하나씩 든 텍스트")
    p.add_argument("--no-score", dest="no_score", action="store_true", help="생성 뒤 채점·보고를 부르지 않는다")
    p.add_argument("--no-probe", dest="no_probe", action="store_true", help="분리 전 확인 호출을 하지 않는다")
    p.add_argument("--foreground", action="store_true", help="분리하지 않고 그 자리에서 돈다")
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    add_model_arguments(p)

    p = sub.add_parser("status", help="run 진행 상태(10줄 안)")
    p.add_argument("--run-id", dest="run_id", metavar="이름")

    p = sub.add_parser("stop", help="도는 run을 멈춘다")
    p.add_argument("--run-id", dest="run_id", metavar="이름")

    p = sub.add_parser("resume", help="멈춘 run을 이어 돈다(채점 전, 같은 코드일 때만)")
    p.add_argument("--run-id", dest="run_id", metavar="이름")
    p.add_argument("--foreground", action="store_true", help="분리하지 않고 그 자리에서 돈다")

    p = sub.add_parser("score", help="공식 채점기로 채점한다(정답 있는 데이터셋만)")
    p.add_argument("--run", metavar="이름", help="없으면 가장 최근 run")
    p.add_argument("--predictions", metavar="파일", help="예측 파일(.json 또는 .jsonl)을 받아 새 run으로 채점")
    p.add_argument("--run-id", dest="run_id", metavar="이름", help="--predictions로 만들 run 이름")
    p.add_argument("--num-cpus", dest="num_cpus", type=int, default=4)
    p.add_argument("--meta-time-out", dest="meta_time_out", type=float, default=30.0)

    p = sub.add_parser("report", help="채점 결과와 기준 비교(정답이 없으면 실행 요약)")
    p.add_argument("--run", metavar="이름", help="없으면 가장 최근 run")
    p.add_argument("--against", metavar="기준", help="기준선 .json, run 이름, 또는 ex.jsonl 경로")

    p = sub.add_parser("leak-audit", help="프롬프트에 정답이 들어갔는지 검사")
    p.add_argument("--run", metavar="이름", help="없으면 가장 최근 run")

    p = sub.add_parser("fetch", help="BIRD 자료·임베딩 모델 받기")
    p.add_argument("what", choices=["bird", "model"])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0) if e.code in (0, None) else EXIT_USAGE
    if not args.command:
        parser.print_help()
        return EXIT_USAGE
    try:
        if args.command == "ask":
            from t2s.cli import ask_cmd

            return ask_cmd.run(args)
        if args.command in ("run", "status", "stop", "resume"):
            from t2s.cli import run_cmds

            return run_cmds.run(args)
        if args.command in ("score", "report", "leak-audit"):
            from t2s.cli import eval_cmds

            return eval_cmds.run(args)
        if args.command == "fetch":
            from t2s.cli import fetch_cmd

            return fetch_cmd.run(args)
        from t2s.cli import data_cmds

        return data_cmds.run(args)
    except BrokenPipeError:
        # 출력을 `| head`로 자른 경우. 남은 출력은 버린다.
        import os

        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
