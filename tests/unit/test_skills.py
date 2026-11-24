"""스킬 글 검사: plugin/skills(스킬 글의 원본)의 글 속 명령이 실제 하위 명령과 맞고, 공통 묶음이 같고, 라우팅 문항 글이 있다.

옛 두 자리(.claude/skills, .agents/skills)는 지웠다. 옛 글과 견주던 테스트도 함께 뺐다(ALLOWED는 기록으로 남긴다).
"""

import argparse
import re
from pathlib import Path

from t2s.cli.main import build_parser

REPO = Path(__file__).resolve().parents[2]
SKILLS = ("getbird", "getolist", "getmetrics", "connect", "text2sql", "runset", "newmodel", "sessions")
# 지표 길의 도구(text2sql-plugin의 scripts/t2s)를 부르는 스킬과, 그 도구에서 부르는 명령·인자(export 하나뿐)
T2S_CALLERS = ("connect", "getmetrics")
T2S_CALL = re.compile(r"scripts/t2s ([a-z][a-z-]*)")
T2S_FLAGS = {"--out"}
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("하위 명령이 없다")


def _all_flags(parser: argparse.ArgumentParser) -> set[str]:
    """한 하위 명령의 인자와, 그 아래 하위 명령(session open·note·close)의 인자."""
    flags: set[str] = set()
    for a in parser._actions:
        flags.update(a.option_strings)
        if isinstance(a, argparse._SubParsersAction):
            for sub in a.choices.values():
                flags |= _all_flags(sub)
    return flags


def _text(where: str, name: str) -> str:
    return (REPO / where / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


# ── plugin/skills 기준(스킬화) ──

ENGINE_SKILL = "p3-engine"
SCRIPT_ONLY = {"setup"}  # t2s가 아니라 실행 스크립트 scripts/p3가 받는 명령
P3_COMMAND = re.compile(r"(?<![\w/.-])p3 ([a-z][a-z-]*)")
PREP_LINE = re.compile(r"^준비: .*$", re.MULTILINE)
KEEP_BLOCK = re.compile(r"^## 지킬 것\n.*?(?=^## )", re.MULTILINE | re.DOTALL)
ROOT_SENTENCE = " 모든 명령은 저장소 루트(`harness.toml`이 있는 폴더)에서 돌린다."
PATH_WORD = re.compile(r"(?<![\w./])(gold|data)/")
STATE_WORD = re.compile(r"(?<![\w./])var/state\.json")
INIT_ASK = '명령이 "작업 폴더가 없습니다. 먼저 init"으로 끝나면 `p3 init`을 돌릴지 한 줄로 묻고, 좋다고 하면 `p3 init` 뒤 다시 실행한다.'
PURE = "문항의 evidence와 사람이 쓴 설명을 모델에 주지 않는 조건"
SWAP_SRC = "플러그인을 업데이트하거나 엔진 스킬 폴더를 건드리지 않는다."
# 게이트 2 사람 결정 (가): 설치본의 채점 run은 허용하되, 점수를 보는 자리에 이 한 줄을 둔다(runset·newmodel 같은 글)
NOT_REPRESENTATIVE = "플러그인 설치본에서 잰 점수는 대표 측정이 아니다. 대표 측정은 text2sql-py 저장소 클론에서 한다."
RUNSET_REPORT = '출력을 그대로 보여 준다. 라벨 전체 문자열과 "새로 맞음·새로 틀림" 문장을 자르지 않는다.\n'
# 정답 없는 데이터셋에서 route-judge가 내는 줄(종료 코드 4)을 newmodel 판정 절에 한 줄 더한다
NEWMODEL_NO_GOLD = (
    '- "이 데이터셋은 정답이 없어 경로를 판정할 수 없습니다"가 나오면(종료 코드 4) 채점하지 않고 그 줄을 그대로 '
    "보여 준 뒤 멈춘다. 기록하지 않는다.\n"
)
NEWMODEL_EVIDENCE = '- "evidence 조건이 달라 견주지 않는다"가 나오면 그대로 보여 주고 멈춘다.\n'
NEWMODEL_JUDGE = (
    '출력을 그대로 보여 준다. 두 run의 라벨 전체와 "새로 맞음·새로 틀림" 문장, 비용 줄, 경로 판정 줄을 자르지 않는다.\n'
)
# 옛 글 → 새 글에서 허용된 스킬별 바꿈(공통 바꿈은 allowed_transform이 한다). 옛 글을 지운 뒤로는 견주는 테스트 없이 기록으로 남긴다.
ALLOWED = {
    "connect": [
        (
            "## 목록\n",
            (
                '## 작업 폴더 확인\n\n첫 명령이 "작업 폴더가 없습니다. 먼저 init"으로 끝나면(종료 코드 2) '
                '"이 폴더에 작업 폴더(`.text2sql/`)를 만들까요?"를 한 줄로 묻는다. 좋다고 하면 `p3 init`을 돌린 뒤 '
                "하려던 명령을 다시 한다. 싫다고 하면 멈춘다.\n\n## 목록\n"
            ),
        ),
        ("퓨어 조건(AGENTS 규칙 9)에서", f"퓨어 조건({PURE})에서"),
    ],
    "getbird": [
        ("새 클론이거나 data/·gold/가 비어 있을 때, t2s datasets가", "작업 폴더에 BIRD 자료가 없을 때, p3 datasets가"),
        ("1. 저장소 루트(`harness.toml`이 있는 폴더)에서 실행한다:", "1. 실행한다:"),
        ("evidence used run에서만 쓴다. 이미 BIRD", f"evidence used run에서만 쓴다. {INIT_ASK} 이미 BIRD"),
        (
            "- `src/t2s/fetch/bird_minidev.json`의 해시를 고치거나, 해시를 건너뛰는 옵션·환경변수를 더하지 않는다.",
            "- 엔진 스킬 폴더의 파일(받는 곳과 해시를 고정한 설정)을 고치지 않는다. 해시를 건너뛰는 옵션·환경변수를 더하지 않는다.",
        ),
    ],
    "getolist": [
        ("새 클론이거나 data/olist/·gold/olist/가 비어 있을 때", "작업 폴더에 Olist 자료가 없을 때"),
        ("1. 저장소 루트(`harness.toml`이 있는 폴더)에서 실행한다:", "1. 실행한다:"),
        (
            "받는 곳과 해시는 `src/t2s/fetch/olist_study.json`에 고정돼 있다.",
            f"받는 곳과 해시는 엔진에 고정돼 있다. {INIT_ASK}",
        ),
        ("퓨어 조건(AGENTS 규칙 9)에서는", f"퓨어 조건({PURE})에서는"),
        (
            "- `src/t2s/fetch/olist_study.json`의 커밋·해시를 고치거나, 해시를 건너뛰는 옵션·환경변수를 더하지 않는다.",
            "- 엔진 스킬 폴더의 파일(받는 곳의 커밋과 해시를 고정한 설정)을 고치지 않는다. 해시를 건너뛰는 옵션·환경변수를 더하지 않는다.",
        ),
    ],
    "text2sql": [
        ("세션 모델이 직접 SQL을 쓰지 않고 t2s ask를 돌린다.", "세션 모델이 직접 SQL을 쓰지 않고 p3 ask를 돌린다."),
        ("이 스킬은 `t2s ask`를 한 번 돌리고", "이 스킬은 `p3 ask`를 한 번 돌리고"),
    ],
    "runset": [
        (RUNSET_REPORT, f"{RUNSET_REPORT}\n{NOT_REPRESENTATIVE}\n"),
        ("`t2s run`(start) → `t2s status` → `t2s report`만", "`p3 run`(start) → `p3 status` → `p3 report`만"),
        ("- run이 도는 동안 `src/` 아래를 바꾸는 작업을 함께 하지 않는다.", f"- run이 도는 동안 {SWAP_SRC}"),
    ],
    "newmodel": [
        (NEWMODEL_JUDGE, f"{NEWMODEL_JUDGE}\n{NOT_REPRESENTATIVE}\n"),
        (NEWMODEL_EVIDENCE, NEWMODEL_NO_GOLD + NEWMODEL_EVIDENCE),
        (
            "`t2s route-start` → `t2s route-status` → `t2s route-judge` → (기록할 수 있는 판정이면 바로) `t2s route-apply`만",
            "`p3 route-start` → `p3 route-status` → `p3 route-judge` → (기록할 수 있는 판정이면 바로) `p3 route-apply`만",
        ),
        (
            'AGENTS 규칙 9의 "퓨어 조건"(evidence not_used)과 다른 말이다.',
            f'"퓨어 조건"(evidence not_used: {PURE})과 다른 말이다.',
        ),
        (
            (
                '3. 쓴 뒤 "harness.toml이 바뀌었습니다. 커밋하기 전의 run에는 (미커밋 코드)가 붙습니다. 커밋은 말씀하실 때 '
                '합니다"를 알린다. 커밋하지 않는다.'
            ),
            (
                "3. 쓴 뒤 따로 지어 낸 안내를 덧붙이지 않고, 명령의 마지막 줄(쓴 것과 커밋 안내)을 그대로 보여 준다. "
                "플러그인 설치본에서는 작업 폴더의 `.text2sql/harness.toml`에 쓴다. 커밋하지 않는다."
            ),
        ),
        ("- 두 run이 도는 동안 `src/` 아래를 바꾸는 작업을 함께 하지 않는다.", f"- 두 run이 도는 동안 {SWAP_SRC}"),
    ],
}


def _new(name: str) -> str:
    return _text("plugin", name)


def _prep(text: str) -> str:
    found = PREP_LINE.findall(text)
    assert len(found) == 1, found
    return found[0]


def _keep(text: str) -> str:
    m = KEEP_BLOCK.search(text)
    assert m, "지킬 것 묶음 없음"
    return m.group(0)


def allowed_transform(name: str, old: str, prep: str, keep: str) -> str:
    """옛 글에 허용된 바꿈만 적용한 글. 새 글과 글자까지 같아야 한다."""
    text = old
    for a, b in ALLOWED[name]:
        assert text.count(a) == 1, (name, a)
        text = text.replace(a, b)
    text = text.replace(ROOT_SENTENCE, "", 1)
    text = text.replace("uv run t2s ", "p3 ")
    text = PATH_WORD.sub(r".text2sql/\1/", text)
    text = STATE_WORD.sub(".text2sql/var/state.json", text)
    head, sep, body = text.partition("\n# ")
    title, _, rest = body.partition("\n\n")
    first, _, after = rest.partition("\n\n")
    text = head + sep + title + "\n\n" + first + "\n\n" + prep + "\n\n" + after
    return text.replace("## 하지 말 것\n", keep + "## 하지 말 것\n", 1)


def test_plugin_common_blocks_same():
    preps = {_prep(_new(name)) for name in SKILLS}
    keeps = {_keep(_new(name)) for name in SKILLS}
    assert len(preps) == 1 and len(keeps) == 1
    prep, keep = preps.pop(), keeps.pop()
    for word in ("`p3-engine`의 `scripts/p3`를 `sh`로 부른다", "`p3 init`", "`$이름`", "`/text2sql:이름`"):
        assert word in prep, word
    for word in (
        ".text2sql/gold/",
        "hints.public.json",
        "evidence not_used",
        "승인을 받은 뒤에만 등록",
        "엔진 소스를 읽지 않는다",
    ):
        assert word in keep, word


def test_plugin_engine_skill_marked_not_auto():
    text = _new(ENGINE_SKILL)
    head = text.split("\n---\n", 1)[0]
    assert text.startswith(f"---\nname: {ENGINE_SKILL}\n") and "\ndisable-model-invocation: true" in head
    assert "소스(`src/`)와 `assets/`를 읽지 않는다" in text


def test_plugin_seven_texts_no_repo_root_or_uv_run():
    for name in (*SKILLS, ENGINE_SKILL):
        text = _new(name)
        assert text.startswith(f"---\nname: {name}\n"), name
        assert "uv run" not in text and "저장소 루트" not in text and "§" not in text, name
    refs = REPO / "plugin" / "skills" / ENGINE_SKILL / "references"
    for path in sorted(refs.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert "uv run" not in text and "저장소 루트" not in text, path.name


def test_plugin_commands_exist():
    subs = _subparsers()
    flags = {opt for p in subs.values() for opt in _all_flags(p)}
    for name in (*SKILLS, ENGINE_SKILL):
        text = _new(name)
        for cmd in P3_COMMAND.findall(text):
            assert cmd in subs or cmd in SCRIPT_ONLY, (name, cmd)
        for flag in FLAG.findall(text):
            assert flag in flags or (name in T2S_CALLERS and flag in T2S_FLAGS), (name, flag)


def test_plugin_newmodel_and_model_rules_kept():
    text = _new("newmodel")
    assert "p3 route-apply --yes" in text and "묻지 않고 바로" in text and "기록할 수 없는 판정" in text
    assert "사용자 승인 없이" not in text and "승인했을 때만" not in text
    assert "기록에 쓸 수 없음" in text and "p3-ref" in text and "커밋하지 않는다" in text
    for name in ("text2sql", "runset"):
        text = _new(name)
        assert "`--effort medium --effort-source default-medium`" in text, name
        assert "추론 단계를 기본값 medium으로 보고 아래의 모델 이름 맞추기를 한다" in text, name
        assert "추론 단계를 말하지 않았으면 medium으로 보고" in text and "`--profile p3-ref`" in text, name
        assert "맞추기를 하지 않는다" not in text, name
    text = _new("runset")
    assert "`p3 run`(start) → `p3 status` → `p3 report`만" in text and "말이 없으면 not_used다" in text


def test_routing_eval_prompts():
    """겹 3: 라우팅 문항 글(돌리지 않는다. 돌리는 것은 승인을 받아 한다)."""
    evals = REPO / "plugin" / "for-plugin-owner" / "evals"
    want = {
        "bird-fetch-routes-to-getbird": ("BIRD 데이터 받아줘", "getbird"),
        "olist-fetch-routes-to-getolist": ("Olist 데이터 받아줘", "getolist"),
        "db-attach-routes-to-connect": ("이 DB 붙여줘", "connect"),
        "data-question-routes-to-text2sql": ("financial에서 계좌 몇 개야", "text2sql"),
        "run-all-routes-to-runset": ("전체 돌려줘", "runset"),
        "new-model-routes-to-newmodel": ("새 모델 재줘", "newmodel"),
        "sessions-list-routes-to-sessions": ("세션 목록 보여줘", "sessions"),
        "metrics-sync-routes-to-getmetrics": ("지표 정의 받아줘", "getmetrics"),
    }
    assert sorted(p.name for p in evals.iterdir() if p.is_dir() and p.name != "results") == sorted(want)
    for case, (prompt, skill) in want.items():
        text = (evals / case / "prompt.md").read_text(encoding="utf-8")
        assert text.startswith("---\ndescription: ") and "allowed_tools: [Skill]" in text
        assert text.rstrip().endswith(prompt), case
        grader = (evals / case / "graders" / "skill-fired.md").read_text(encoding="utf-8")
        pattern = re.search(r"input_match: '(.*)'", grader).group(1)
        assert re.search(pattern, f'{{"skill": "text2sql:{skill}"}}') and re.search(pattern, f'{{"skill": "{skill}"}}')
        assert not re.search(pattern, '{"skill": "text2sql:p3-engine"}'), case


def test_plugin_sessions_calls_only_allowed_commands():
    """AGENTS 규칙 13: /sessions는 sessions·status·resume·stop·report만 부른다(공통 묶음의 init·ask·run 말은 뺀다)."""
    text = _new("sessions")
    body = text.replace(_prep(text), "").replace(_keep(text), "")
    assert set(P3_COMMAND.findall(body)) == {"sessions", "status", "resume", "stop", "report"}
    assert "세션을 지우거나 이름을 바꾸거나 옮기지 않는다" in text
    assert "`session.json`" in text and "`t2s-log.jsonl`" in text and "열지 않는다" in text
    for word in (
        "p3 status --run-id '<세션 이름>'",
        "p3 report --run '<세션 이름>'",
        "p3 resume --run-id '<세션 이름>'",
    ):
        assert word in text, word


def test_engine_skill_lists_session_commands():
    text = _new(ENGINE_SKILL)
    assert "sessions)이" in text.split("\n---\n", 1)[0]
    assert "`p3 sessions`" in text and "`p3 session open --kind <종류>`" in text
    layout = (REPO / "plugin/skills" / ENGINE_SKILL / "references/session-layout.md").read_text(encoding="utf-8")
    assert "YYYY-MM-DD-<데이터셋>-<종류>-<16진 6자>" in layout and "legacy_runs" in layout and "index.jsonl" in layout
    assert ".text2sql/runs/" not in layout


def test_plugin_user_text_single_quoted():
    """리뷰 게이트 4: 사용자가 쓴 질문은 셸 작은따옴표로 넘긴다(큰따옴표 안에서는 셸이 $·`·\\를 바꾼다)."""
    text = _new("text2sql")
    assert "\n   p3 ask [--source '<이름>'] <모델 인자> '<질문>'\n" in text
    assert "`'\\''`" in text and "큰따옴표는 쓰지 않는다" in text
    engine = _new(ENGINE_SKILL)
    assert "`p3 ask [--source '<DB>'] <모델 인자> '<질문>'`" in engine and "`'\\''`" in engine
    for name in (*SKILLS, ENGINE_SKILL):
        assert '"<질문>"' not in _new(name), name
    connect = _new("connect")
    assert "p3 register '<이름>' --sqlite '<경로>' --yes" in connect and "`'\\''`" in connect
    assert "`--questions '<경로>'`" in _new("runset")
    assert "/connect bird로 BIRD를 지금 데이터셋으로 정한 뒤" in _new("getbird")


def test_plugin_codex_sandbox_note():
    """Codex 샌드박스 오류 때 엔진이 내는 안내 줄과 오류 글을 text2sql·runset·명령 표가 같은 글자로 가리킨다."""
    from t2s.cli.data_cmds import CODEX_SANDBOX_HINT, CODEX_SANDBOX_MARK

    head = CODEX_SANDBOX_HINT.split(". ")[0]
    commands = (REPO / "plugin/skills" / ENGINE_SKILL / "references/commands.md").read_text(encoding="utf-8")
    for text in (_new("text2sql"), _new("runset"), commands):
        assert head in text and CODEX_SANDBOX_MARK in text and "전체 접근" in text
    for name in ("text2sql", "runset"):
        assert '샌드박스 없는 터미널에서)만 돕니다"라고 알리고 멈춘다. 다시 돌리지 않는다' in _new(name)


def test_plugin_failure_stop_rule():
    """p3 ask(runset은 p3 명령)가 실패하면 다른 방법으로 답하지 않는다: SQL 직접 쓰기·자료/DB 파일 읽기·웹 검색 금지."""
    text = _new("text2sql")
    assert (
        "`p3 ask`가 실패하면 질문에 다른 방법으로 답하지 않는다. SQL을 직접 쓰지 않고, 자료 폴더나 DB 파일을 직접 읽거나 "
        "둘러보지 않고, 웹을 검색하지 않는다. 오류를 보여 주고 멈춘다." in text
    )
    dont = text.partition("## 하지 말 것\n")[2]
    assert dont.startswith(
        "\n- `p3 ask`가 실패했을 때 답을 대신 찾지 않는다. SQL을 직접 쓰지 않고, 자료 폴더나 DB 파일을 직접 읽지 않고, "
        "웹을 검색하지 않는다. 오류를 보여 주고 멈춘다. 다만 5번의 절차("
    )
    assert (
        "- `p3` 명령이 실패했을 때 결과를 다른 방법으로 만들지 않는다. SQL을 직접 쓰지 않고, 자료 폴더나 DB 파일을 직접 "
        "읽지 않고, 웹을 검색하지 않는다. 오류를 보여 주고 멈춘다." in _new("runset").partition("## 하지 말 것\n")[2]
    )


# ── semantic used 조건(계획 2-7·2-8, AGENTS 규칙 13) ──

# 계획 2-7절의 알림 글(정해진 글자). 앞의 둘은 p3 semantic-check가 낸다
METRIC_NOTICES = (
    (
        "이 DB에 맞는 활성 지표 모델이 있습니다: <모델 이름> (판 <version_id 앞 8자>, 승인 지표 <N>개). "
        "지표 질문은 /ask로 물으세요. /text2sql과 /runset의 SQL 생성에는 이 정의를 넣지 않습니다."
    ),
    "활성 지표 모델 <모델 이름>은 이 DB와 맞지 않습니다(모델의 테이블 <M>개 가운데 <K>개가 이 DB에 없음).",
    "Graph MCP에 연결하지 못해 이 DB에 맞는 지표 모델이 있는지 확인하지 못했습니다. DB 등록은 끝났습니다.",
    "Graph MCP에 활성 지표 모델이 없습니다.",
    "지표 길 도구를 쓸 수 없어 지표 모델 확인을 건너뜁니다. DB 등록은 끝났습니다.",
    "활성 지표 모델의 승인 지표 가운데 SQL로 바꿀 수 없는 것이 있어 지표 모델을 확인하지 못했습니다. DB 등록은 끝났습니다.",
)
T2S_NOTE = (
    "플러그인 폴더의 `scripts/t2s`는 지표 길의 도구다(엔진의 `p3`와 다른 프로그램이다). 여기서는 `export` 하나만 부른다. "
    "명령 출력의 `t2s <명령>`을 `p3 <명령>`으로 읽는 규칙은 엔진 출력에만 해당한다."
)
SEMANTIC_PHRASES = '"지표 정의 넣어서", "지표 정의 써서", "semantic 써서", "시맨틱 써서", "semantic used로"'


def test_connect_metric_notices_match_plan():
    text = _new("connect")
    for line in METRIC_NOTICES:
        assert f"`{line}`" in text, line
    for kind in ("`graph_unavailable`", "`no_active_model`", "`no_uv`", "`setup_failed`", "`bad_definition`"):
        assert kind in text, kind
    assert "\n   p3 semantic-check '<이름>'\n" in text and T2S_NOTE in text
    assert "`/getmetrics`나 semantic used를 권하지 않는다" in text and "Graph MCP 도구를 직접 부르지 않고" in text
    assert "확인이 실패해도 connect는 실패가 아니다" in text and "`DATABASE_URL`은 주지 않는다" in text
    section = text.partition("## 지표 모델 확인 (알리기만)")[2].partition("\n## ")[0]
    assert "semantic-import" not in section and "--semantic" not in section


def test_engine_check_lines_match_plan_notices():
    """connect가 보이는 앞 두 줄은 엔진(semantic-check)이 내는 글과 틀이 같다."""
    import inspect

    from t2s.cli import data_cmds

    src = inspect.getsource(data_cmds.cmd_semantic_check)
    assert "이 DB에 맞는 활성 지표 모델이 있습니다: {model} (판 " in src
    assert (
        "지표 질문은 /ask로 물으세요. " in src and "/text2sql과 /runset의 SQL 생성에는 이 정의를 넣지 않습니다." in src
    )
    assert (
        "활성 지표 모델 {model}은 이 DB와 맞지 않습니다(모델의 테이블 {total}개 가운데 {missing}개가 이 DB에 없음)."
        in src
    )


def test_t2s_only_in_connect_and_getmetrics_and_only_export():
    for name in (*SKILLS, ENGINE_SKILL):
        text = _new(name)
        if name in T2S_CALLERS:
            assert set(T2S_CALL.findall(text)) == {"export"}, name
            assert T2S_NOTE in text, name
            assert (
                "sh <이 SKILL.md가 있는 폴더>/../../scripts/t2s export --out '.text2sql/var/semantic-inbox/<이름>.json'"
                in text
            ), name
        else:
            assert "scripts/t2s" not in text, name


def test_getmetrics_flow():
    text = _new("getmetrics")
    assert "지표 정의 받아줘" in text and "시맨틱 정의 가져와" in text and "지표 정의 넣어서 돌릴 준비" in text
    assert "/ask" in text and "매출 얼마야" in text
    for line in METRIC_NOTICES[2:]:
        assert f"`{line}`" in text, line
    for word in (
        "\n   p3 datasets\n",
        "\n   p3 semantic-import '<이름>'\n",
        "\n   p3 semantic-import '<이름>' --yes\n",
        "\n   p3 semantic-remove '<이름>' --inbox\n",
        "BIRD Mini-Dev에는 지표 정의가 없습니다",
        "진행할까요?",
        "사용자에게 칠 명령으로 보이지 않는다",
        "사용자가 진행하라고 답한 뒤에만",
        "로컬 정의 파일(`*.ossie.yaml` 같은 것)",
        "지표 정의를 넣어 돌리려면 /text2sql이나 /runset에 '지표 정의 넣어서'라고 말하세요. 말하지 않으면 넣지 않습니다.",
        "승인 전 지표를 쓰자고 권하지 않는다",
    ):
        assert word in text, word
    # 승인 앞에서는 --yes를 돌리지 않는다: --yes 줄은 "진행하라고 답한 뒤에만" 뒤에만 나온다
    before = text.partition("사용자가 진행하라고 답한 뒤에만")[0]
    assert "p3 semantic-import '<이름>' --yes" not in before


def test_semantic_opt_in_in_text2sql_and_runset():
    for name in ("text2sql", "runset"):
        text = _new(name)
        section = text.partition("## 지표 정의 정하기 (사용자에게 인자를 묻지 않는다)")[2].partition("\n## ")[0]
        assert "기본은 semantic not_used다. 받아들인 지표 정의가 있어도 말이 없으면 넣지 않는다." in section, name
        assert f"{SEMANTIC_PHRASES}처럼 지표 정의를 넣으라고 말했을 때만 `--semantic used`를 붙인다" in section
        assert '"evidence와 지표 정의는 함께 쓰지 않습니다. 어느 쪽으로 돌릴까요?"' in section
        for word in ("받아들인 지표 정의가 없습니다", "BIRD Mini-Dev에는 지표 정의가 없습니다", "함께 쓰지 않습니다"):
            assert f'"{word}"' in section, (name, word)
        assert "`--semantic`" in text.partition("## 하지 말 것\n")[2], name
    runset = _new("runset")
    assert "비교 대상 없음(기준은 semantic not_used, 이번은 used라 견주지 않는다)" in runset


def test_common_keep_has_semantic_lines():
    keep = _keep(_new("connect"))
    assert "- 기본은 semantic not_used다. 사용자가 지표 정의를 넣으라고 말했을 때만 semantic used로 돈다." in keep
    assert "통째로 읽거나 내용을 출력하지 않는다. 명령이 낸 요약 줄만 보인다." in keep


def test_engine_guides_mention_semantic():
    refs = REPO / "plugin/skills" / ENGINE_SKILL / "references"
    commands = (refs / "commands.md").read_text(encoding="utf-8")
    for word in ("`semantic-check`", "`semantic-import`", "`semantic-remove`", "`--semantic used`"):
        assert word in commands, word
    egress = (refs / "egress.md").read_text(encoding="utf-8")
    assert "semantic used일 때는 받아들인 지표 정의 글" in egress and "토큰과 모델 이름만 보내고" in egress
    labels = (refs / "labels.md").read_text(encoding="utf-8")
    assert "/ semantic used(<받아들인 정의 파일의 sha256 앞 8자>)" in labels and "시험용 정의·점수 아님" in labels
    assert "`references/semantic-file.md`" in _new(ENGINE_SKILL)
