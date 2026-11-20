"""DB 두 묶음 고정 배정. 문항 수와 난이도 분포만 보고 정한다(정답·EX를 보지 않는다)."""

from __future__ import annotations

import itertools

LEVELS = ("simple", "moderate", "challenging")


def objective(counts: dict[str, dict[str, int]], group_a: set[str]) -> int:
    """|2·n_A − N| + Σ_난이도 |2·n_A,난이도 − N_난이도|."""
    total = sum(sum(c.values()) for c in counts.values())
    n_a = sum(sum(counts[d].values()) for d in group_a)
    out = abs(2 * n_a - total)
    for lv in LEVELS:
        all_lv = sum(c.get(lv, 0) for c in counts.values())
        a_lv = sum(counts[d].get(lv, 0) for d in group_a)
        out += abs(2 * a_lv - all_lv)
    return out


def assign(counts: dict[str, dict[str, int]]) -> dict:
    """counts: DB → 난이도 → 문항 수. 돌려주는 것: A·B 목록(글자 순), 목적값, 같은 목적값의 배정 수."""
    names = sorted(counts)
    if len(names) < 2:
        raise ValueError("DB가 둘 이상이어야 묶음을 나눌 수 있다")
    first, rest = names[0], names[1:]
    best: tuple[int, tuple[str, ...]] | None = None
    ties = 0
    for mask in itertools.product((0, 1), repeat=len(rest)):
        group_a = tuple(sorted([first, *[d for d, m in zip(rest, mask, strict=True) if m]]))
        if len(group_a) == len(names):
            continue
        key = (objective(counts, set(group_a)), group_a)
        if best is None or key[0] < best[0]:
            best, ties = key, 1
        elif key[0] == best[0]:
            ties += 1
            if key[1] < best[1]:
                best = key
    assert best is not None
    group_a = list(best[1])
    group_b = [d for d in names if d not in best[1]]
    return {"A": group_a, "B": group_b, "objective": best[0], "ties": ties}


def summarize(counts: dict[str, dict[str, int]], group: list[str]) -> dict:
    out = {"count": sum(sum(counts[d].values()) for d in group)}
    for lv in LEVELS:
        out[lv] = sum(counts[d].get(lv, 0) for d in group)
    return out
