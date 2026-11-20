"""summary.json에서 보고서에 옮길 마크다운 표(tables.md)를 만든다. SQL·행 내용은 넣지 않는다."""

from __future__ import annotations

LABEL = (
    "BIRD Mini-Dev / SQLite / cc48ba16 / 500문제 / EX / evidence not_used / 로컬 측정 / "
    "{model}·추론 medium·codex-cli / 1회 실행 / 구현 {impl}"
)
RECOMB = "기존 run 재조합(새 측정 아님)"


def _n(x) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:,.0f}" if abs(x) >= 100 else f"{x:.3f}"
    return f"{x:,}"


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def mark(name: str, flags_of: dict) -> str:
    """조합 이름 뒤 표시: 부풂은 (부풂), 부분합·미기록은 †."""
    out = name
    parts = name.split(":")[-1].split(">") if ":" in name else [name]
    if any("inflated" in flags_of.get(p, []) for p in parts):
        out += " (부풂)"
    if any(set(flags_of.get(p, [])) & {"partial", "missing_item"} for p in parts):
        out += " †"
    return out


def label(c: dict) -> str:
    info = c["info"]
    impl = f"Python {info['commit']}" if c["kind"] == "p3" else f"기준선 프로젝트 {info['commit']}(P3 구현 아님)"
    return LABEL.format(model=c["model"], impl=impl)


def _row_summary(s: dict, flags_of: dict) -> str:
    return (
        f"| {mark(s['name'], flags_of)} | {s['correct']} | {s['loss']} | {_n(s['tok'])} | {_pct(s['saving'])} | "
        f"{_pct(s['time_saving'])} | {s['newly_correct']} | {s['regressed']} | {s['p']:.3f} | "
        f"{'통과' if s['loss_ok'] else '탈락'} | {'통과' if s['saving_ok'] else '미달'} |"
    )


HEAD = (
    "| 후보 | 정답 | 손실 | 총 토큰 | 토큰 절감 | 시간 절감 | 새로 맞음 | 새로 틀림 | p | 손실 기준 | 절감 15% |\n"
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"
)


def tables(S: dict) -> str:
    flags_of = {k: v["flags"] for k, v in S["combos"].items()}
    L: list[str] = ["# 라우팅 1단계 표(자동 생성)", "", f"모든 라우터 행: {RECOMB}.", ""]

    L += ["## 조합 8개", ""]
    L += [
        (
            "| 조합 | 모델 | 하네스 | 커밋·스냅샷 | CLI 지시 설정 | 프로필 | Codex CLI | Python | SQLite | 정답/500 | "
            "입력 토큰 | 출력 토큰 | 총 토큰 | 문항 시간 합(초) | 생성 예외 | 토큰 표시 |"
        ),
        "|---|---|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, c in S["combos"].items():
        t, i = c["totals"], c["info"]
        L.append(
            f"| {mark(name, flags_of)} | {c['model']} | {'P3' if c['kind'] == 'p3' else '순수'} | {i['commit']} | "
            f"{i['harness']} | {i.get('profile') or '없음'} | {i.get('codex')} | {i.get('python')} | {i.get('sqlite')} | "
            f"{t['correct']} | {_n(t['tok_in'])} | {_n(t['tok_out'])} | {_n(t['tok'])} | {t['ms'] / 1000:,.0f} | "
            f"{t['gen_exceptions']} | {','.join(c['flags']) or '완전'} |"
        )
    L += ["", "라벨:", ""]
    L += [f"- {name}: `{label(c)}`" for name, c in S["combos"].items()]

    sp = S["split"]
    L += ["", "## 묶음", ""]
    L += ["| 묶음 | DB | 문항 | simple | moderate | challenging |", "|---|---|---:|---:|---:|---:|"]
    for g in ("A", "B"):
        sm = sp[f"{g}_summary"]
        L.append(
            f"| {g} | {', '.join(sp[g])} | {sm['count']} | {sm['simple']} | {sm['moderate']} | {sm['challenging']} |"
        )
    L.append(f"\n목적값 {sp['objective']}, 같은 목적값 배정 {sp['ties']}개.")

    L += ["", "## 조합별 묶음 성적", ""]
    L += [
        "| 조합 | 묶음 | 정답 | 총 토큰 | 시간(초) | 오류 신호 | 0행 신호 | NULL 신호 | 평균 행 F1 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, c in S["combos"].items():
        for g in ("A", "B"):
            b = c["by_group"][g]
            L.append(
                f"| {mark(name, flags_of)} | {g} | {b['correct']} | {_n(b['tok'])} | {b['ms'] / 1000:,.0f} | {b['err']} | "
                f"{b['empty']} | {b['null']} | {b['f1_mean']:.3f} |"
            )

    M = S["main"]
    for k, (tune, conf) in (("1", ("A", "B")), ("2", ("B", "A"))):
        d = M["directions"][k]
        L += ["", f"## 방향 {k}(조정 {tune} → 확인 {conf})", ""]
        L.append(f"비용 순서(조정용 문항당 평균 토큰): {' < '.join(d['order'])}")
        L.append(
            f"후보 수 {d['candidate_count']}({d['candidate_count_by_kind']}), 조정용 손실 기준 통과 {d['tune_pass_count']}개"
        )
        L += ["", HEAD]
        if d["picked"]:
            L.append(_row_summary({**d["picked_tune"], "name": "조정용: " + d["picked"]}, flags_of))
            L.append(_row_summary({**d["picked_conf"], "name": "확인용: " + d["picked"]}, flags_of))
        else:
            L.append(
                _row_summary(
                    {**d["least_loss_tune"], "name": "조정용 손실 최소: " + d["least_loss_tune"]["name"]}, flags_of
                )
            )
        L.append(_row_summary({**d["base_conf"], "name": "확인용 기준 sol62-pure"}, flags_of))
        L.append("")
        L.append(f"판정: {'통과' if d['pass'] else '탈락'} {d.get('fail_reason') or ''}")
        if d.get("picked_conf"):
            pc = d["picked_conf"]
            L.append(f"단계별 도달·올려 보냄(확인용): {pc['reached']} / {pc['escalated']}")
            L.append(
                f"비교 문장(확인용 {pc['n']}문항, 기준 13×n/500={pc['allowed_loss']:.3f}): {pc['sentence']} ({RECOMB})"
            )
        if d.get("random"):
            r = d["random"]
            L.append(
                f"무작위 기준(같은 사슬·같은 비율, 시드 {r['seeds']}개): 정답 평균 {r['correct_mean']:.1f} "
                f"[{r['correct_p2_5']:.1f}, {r['correct_p97_5']:.1f}], 토큰 평균 {_n(r['tok_mean'])}. "
                f"신호가 무작위 97.5 백분위를 넘는가: {'예' if r['beats_random'] else '아니오'}"
            )
        if d.get("all_escalate_check"):
            a = d["all_escalate_check"]
            L.append(
                f"모두 올려 보내기 검사: 정답 {a['correct']}(마지막 조합 단독 {a['last_alone_correct']}), "
                f"토큰 {_n(a['tok'])}(사슬 합 {_n(a['chain_tok_sum'])})"
            )
        L += ["", "단독 조합(확인용, 라우터 아님):", "", HEAD]
        L += [_row_summary(s, flags_of) for s in d["fixed_conf"]]
        L += ["", "참고: 확인용 토큰 절감률 상위 10개 후보(판정에 쓰지 않음):", "", HEAD]
        L += [_row_summary(s, flags_of) for s in d["conf_top"]]
        L += [
            "",
            "참고: 확인용에서 손실 기준을 지킨 후보 가운데 절감률 상위 10개(판정에 쓰지 않음, 확인용을 보고 고른 값이라 낙관적):",
            "",
            HEAD,
        ]
        L += [_row_summary(s, flags_of) for s in d["conf_top_loss_ok"]]

    L += ["", "## 500문항 합산", ""]
    if M.get("combined"):
        c = M["combined"]
        L.append(
            f"정답 {c['correct']}(기준 {c['base_correct']}), 토큰 절감 {_pct(c['saving'])}, 시간 절감 {_pct(c['time_saving'])}, "
            f"새로 맞음 {c['newly_correct']}, 새로 틀림 {c['regressed']}, p={c['p']:.3f} → {c['statement']} ({RECOMB})"
        )
    else:
        L.append("두 방향 가운데 후보를 고르지 못한 방향이 있어 합산하지 않음.")
    L.append(f"두 방향이 같은 후보를 골랐는가: {'예' if M['same_pick'] else '아니오'}")
    if M["cross"]:
        L += ["", "고른 후보의 양쪽 묶음·전체 성적:", "", HEAD]
        for name, x in M["cross"].items():
            for g in ("A", "B", "all"):
                L.append(_row_summary({**x[g], "name": f"{name} [{g}]"}, flags_of))
    L.append(f"\n2단계 후보: {M['stage2'] or '없음(2단계로 가지 않음)'}")

    L += ["", "## 상한(완벽한 라우터)", "", HEAD]
    for g in ("A", "B", "all"):
        o = M["oracles"][g]
        for k in ("oracle-pick-4", "oracle-pick-8", "oracle-esc-best"):
            if o.get(k):
                L.append(_row_summary({**o[k], "name": f"{g}: {o[k]['name']}"}, flags_of))

    L += ["", "## 신호의 질(500문항)", ""]
    L += [
        "| 조합 | 신호 | 신호 난 문항 | 그중 틀림 | 신호 정밀도 | 틀린 문항 중 신호 비율 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name, q in S["signal_quality"].items():
        for s, v in q.items():
            L.append(
                f"| {mark(name, flags_of)} | {s} | {v['fired']} | {v['fired_wrong']} | {_pct(v['precision'])} | {_pct(v['recall'])} |"
            )

    L += ["", "## 행 단위 F1(참고, 판정에 쓰지 않음)", ""]
    L += [
        "| 조합 | 평균 | simple | moderate | challenging | F1 없음 | EX 0·F1≥0.5 | 집합 F1과 공식 EX 어긋남 | 기록 실행 상태 일치 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, f in S["f1"].items():
        b = f["by_difficulty"]
        L.append(
            f"| {mark(name, flags_of)} | {f['mean']:.3f} | {b['simple']:.3f} | {b['moderate']:.3f} | {b['challenging']:.3f} | "
            f"{f['none']} | {f['ex0_f1_ge_half']} | {f['set_vs_official_mismatch']} | {S['combos'][name]['recorded_exec_agree']}/500 |"
        )
    L.append(f"\n정답 SQL 실행 상태: {S['gold_status']}")

    B = S["breakeven"]
    L += ["", "## 최상위 모델 배분 방식 손익분기점(500문항)", ""]
    L.append(f"기준 총 토큰 {_n(B['base_tok'])}, 문항당 {B['base_mean']:,.0f}")
    L += [
        "",
        "| 배분(완벽한 배분) | 정답 | 상위 비율 f | 배정 토큰 | 그 배분의 d_max(문항당) |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in B["rows"]:
        L.append(
            f"| {mark(r['low'], flags_of)} → sol62-pure | {r['correct']} | {r['f_high']:.3f} | {_n(r['assigned_tok'])} | {r['d_max']:,.0f} |"
        )
    lb = B.get("loss_bound")
    if lb:
        L += ["", f"정답 {lb['min_correct']} 이상을 지키는 배분 가운데 배정 토큰 최소(완벽한 배분 가정):", ""]
        L += ["| 조합 | 정답 | 배정 토큰 | 토큰 미기록(0) 문항 | d_max(문항당) |", "|---|---:|---:|---:|---:|"]
        for r in lb["rows"]:
            if "d_max" not in r:
                L.append(f"| {r['low']} | 정답 하한에 못 미침 | - | - | - |")
                continue
            L.append(
                f"| {r['low']} | {r['correct']} | {_n(r['assigned_tok'])} | {r['zero_tok_items']} | {r['d_max']:,.0f} |"
            )
    L += ["", "비율 격자(배정이 무작위일 때의 기대값):", ""]
    lows = list(B["grid"])
    L += ["| f | " + " | ".join(lows) + " |", "|---:|" + "---:|" * len(lows)]
    for k in range(len(B["grid"][lows[0]])):
        f = B["grid"][lows[0]][k]["f"]
        L.append(f"| {f:.1f} | " + " | ".join(f"{B['grid'][lo][k]['d_max']:,.0f}" for lo in lows) + " |")
    fl = B["floor"]
    L.append(
        f"\nsol62-pure 한 호출 입력 토큰: 최소 {fl['input_min']:,}(그중 캐시 {fl['cached_at_min']:,}), "
        f"중앙값 {fl['input_median']:,.0f}(캐시 중앙값 {fl['cached_median']:,.0f}), 출력 중앙값 {fl['output_median']:,.0f}"
    )

    Se = S["sensitivity"]
    L += ["", "## 민감도(판정에 쓰지 않음)", ""]
    L += ["| 바꾼 것 | 정답 | 짝 | 방향 1 | 방향 2 | 2단계 후보 |", "|---|---:|---|---|---|---|"]

    def cell(v, k):
        c = v["conf"][k]
        if not c:
            return "후보 없음"
        return f"{v['picks'][k]} 손실 {c['loss']} 절감 {_pct(c['saving'])} {'통과' if v['pass'][k] else '탈락'}"

    for name, v in Se["rep_swap"].items():
        L.append(
            f"| 대표 {name} | {v['correct']} | {v['pair']} | {cell(v, '1')} | {cell(v, '2')} | {v['stage2'] or '없음'} |"
        )
    for name, v in Se["pair_swap"].items():
        db = v["dis_best"]
        extra = "; ".join(
            f"방향 {k} 불일치 최선 {x['name']} 손실 {x['loss']} 절감 {_pct(x['saving'])}"
            if x
            else f"방향 {k} 불일치 후보 없음"
            for k, x in db.items()
        )
        L.append(f"| 짝 {name} | - | - | {cell(v, '1')} | {cell(v, '2')} | {v['stage2'] or '없음'} ({extra}) |")
    inf = Se["inflation"]
    L.append(
        f"| 부푼 토큰 보정 배수 {inf['factor']:.3f} | - | - | {cell(inf, '1')} | {cell(inf, '2')} | {inf['stage2'] or '없음'} |"
    )

    L += ["", "## 반복 run 프롬프트 대조", ""]
    L += [
        "| run | 커밋 | CLI 지시 설정 | 정답 | 생성 예외 | 첫 호출 프롬프트 같음 | 생성 프롬프트 같음 | 사용 |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for name, v in S["prompt_check"].items():
        L.append(
            f"| {name} | {v['commit']} | {v['harness']} | {v['correct']} | {v['gen_exceptions']} | {v['same_first_prompt']} | "
            f"{v['same_generate_prompt']} | {'예' if v['kept'] else '아니오'} |"
        )
    L.append(f"\n짝 run: {S['pairs']}, 불일치 문항 수: {S['pair_disagree']}")
    return "\n".join(L) + "\n"
