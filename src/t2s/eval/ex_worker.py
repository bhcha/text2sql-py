"""문항별 EX 판정. 같은 venv의 Python 하위 프로세스로만 돈다(eval/bird.py가 부른다).

출처: TS 하네스 benchmarks/vendor-bridge/ex_worker.py(자체 코드)를 옮겼다. 바꾼 것: 머리말과 사용법 줄.
판정 논리는 없고 고정 커밋 공식 채점기의 함수를 그대로 불러 쓴다(복사하거나 고치지 않는다).
- evaluation_utils.package_sqls: 예측 JSON과 정답 파일을 채점기와 같은 방식으로 읽는다.
- evaluation_ex.execute_model: 채점기가 문항마다 부르는 함수(func_timeout, calculate_ex 집합 비교, 오류·시간 초과는 0점).
채점기의 run_sqls_parallel과 같이 multiprocessing Pool로 돌리되, 결과를 위치마다 남긴다.

사용: python -B ex_worker.py --vendor DIR --predictions P --gold G --db-root R/ --num-cpus 4 --meta-time-out 30.0
      --sql-dialect SQLite --out ex.raw.jsonl
"""

import argparse
import json
import multiprocessing as mp
import sys


def main():
    parser = argparse.ArgumentParser()
    for name in ("--vendor", "--predictions", "--gold", "--db-root", "--out"):
        parser.add_argument(name, required=True)
    parser.add_argument("--num-cpus", type=int, default=4)
    parser.add_argument("--meta-time-out", type=float, default=30.0)
    parser.add_argument("--sql-dialect", default="SQLite")
    args = parser.parse_args()
    sys.path.insert(0, args.vendor)
    from evaluation_ex import execute_model
    from evaluation_utils import package_sqls

    predicted, _ = package_sqls(args.predictions, args.db_root, mode="pred")
    gold, db_paths = package_sqls(args.gold, args.db_root, mode="gt")
    if len(predicted) != len(gold):
        print(f"warning: predictions {len(predicted)} != gold {len(gold)}; zip uses the shorter", file=sys.stderr)
    jobs = [
        (p, g, db_paths[i], i, args.meta_time_out, args.sql_dialect) for i, (p, g) in enumerate(zip(predicted, gold))
    ]
    with mp.Pool(processes=args.num_cpus) as pool:
        results = pool.starmap(execute_model, jobs)
    with open(args.out, "w", encoding="utf-8") as stream:
        stream.writelines(
            json.dumps({"position": result["sql_idx"], "ex_match": result["res"]}) + "\n" for result in results
        )


if __name__ == "__main__":
    main()
