"""스터디 Olist 자료 받기: 비공개 스터디 저장소에서 고정 커밋의 파일 5개를 gh로 받아 크기·sha256을 대조한다.

자체 코드다. TS 하네스의 `harness/fetch-olist.ts`를 Python으로 옮겼다. 바꾼 것: gh 호출을 `subprocess.run`으로,
요약에 단계 번호와 머리줄을 붙임(`format_summary`), 해시 표를 패키지 안 `olist_study.json`으로 옮김.

- 인자를 받지 않는다. 해시를 건너뛰는 옵션·환경변수가 없고, 해시가 다르면 멈춘다.
- 정답 파일은 처음부터 gold/olist/ 안에 받는다(.part도 그 안에 생긴다). 파일 내용은 출력하지 않는다.
- 이어받기는 없다. 남은 .part는 덮어쓴다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from t2s.fetch.download import sha256_file
from t2s.fetch.steps import FetchStopped, format_summary, hash_mismatch, megabytes, short_hash

GH = "gh"  # 테스트는 가짜 실행 파일로 바꾼다
MANIFEST_NAME = "olist_study.json"
MIN_FREE_BYTES = 1024**3


def load_manifest() -> dict:
    return json.loads(resources.files("t2s.fetch").joinpath(MANIFEST_NAME).read_text(encoding="utf-8"))


def _at(root: Path, rel: str) -> Path:
    if os.path.isabs(rel) or ".." in rel.split("/"):
        raise FetchStopped("경로", f"허용되지 않는 경로: {rel}")
    return Path(root) / rel


def _gh(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run([GH, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None


def preflight(root: Path, repo: str) -> str:
    step = "1단계"
    free = shutil.disk_usage(root).free
    if free < MIN_FREE_BYTES:
        raise FetchStopped(step, f"디스크 여유 부족({free / 1024**3:.1f}GB)")
    version = _gh("--version")
    if version is None or version.returncode != 0:
        raise FetchStopped(step, "gh 없음", "GitHub CLI(gh)를 설치한 뒤 다시 /getolist를 부른다")
    auth = _gh("auth", "status")  # 출력에 계정 이름이 섞이므로 종료 코드만 본다
    if auth is None or auth.returncode != 0:
        raise FetchStopped(
            step, "gh 로그인 안 됨", "터미널에서 `! gh auth login`으로 로그인한 뒤 다시 /getolist를 부른다"
        )
    access = _gh("api", f"repos/{repo}", "--jq", ".full_name")
    if access is None or access.returncode != 0:
        raise FetchStopped(
            step, f"{repo} 접근 권한 없음", "스터디 저장소에 초대된 GitHub 계정으로 로그인했는지 확인한다"
        )
    first = (version.stdout.splitlines() or [""])[0]
    return f"사전 확인: 디스크 여유 {free / 1024**3:.0f}GB, {' '.join(first.split()[:3])}, 저장소 접근 확인"


def _fetch_raw(repo: str, commit: str, remote: str, part: Path) -> int:
    part.parent.mkdir(parents=True, exist_ok=True)
    args = [GH, "api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{remote}?ref={commit}"]
    try:
        with open(part, "wb") as out:
            r = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.PIPE, check=False)
    except FileNotFoundError:
        return 127
    if r.returncode != 0:
        err = (r.stderr.decode("utf-8", "replace").splitlines() or [""])[0]
        print(f"gh: {err[:200]}", file=sys.stderr)
    return r.returncode


def _download(root: Path, repo: str, commit: str, item: dict) -> str:
    """돌려주는 것: "건너뜀" | "받음". 받은 파일은 크기·sha256이 맞을 때만 제 이름이 된다."""
    step = "2단계"
    dest = _at(root, item["path"])
    label = dest.name
    if dest.exists():
        actual = sha256_file(dest)
        if dest.stat().st_size != item["size"] or actual != item["sha256"]:
            raise FetchStopped(
                step,
                hash_mismatch(f"이미 있는 {label}", item["sha256"], actual),
                f"{item['path']}를 확인하고 지운 뒤 다시 /getolist를 부른다",
            )
        return "건너뜀"
    part = dest.with_name(dest.name + ".part")
    code = _fetch_raw(repo, commit, item["remote"], part)
    if code != 0:
        part.unlink(missing_ok=True)
        raise FetchStopped(step, f"{label} 받기 실패(gh {code})", "다시 /getolist를 부른다")
    actual = sha256_file(part)
    if part.stat().st_size != item["size"] or actual != item["sha256"]:
        rejected = dest.with_name(f"{dest.name}.rejected-{short_hash(actual)}")
        os.replace(part, rejected)
        raise FetchStopped(
            step,
            hash_mismatch(label, item["sha256"], actual),
            f"받은 파일은 {rejected.name}로 남겼다. 원격 자료가 바뀌었을 수 있으니 받아들이지 않는다",
        )
    os.replace(part, dest)
    return "받음"


def _check_model_inputs(root: Path, entry: dict, allowed: list[str]) -> int:
    """모델 입력 파일에 정답 쪽 키가 섞여 있지 않은지 키 이름만 확인한다(내용은 출력하지 않는다)."""
    step = "3단계"
    data = json.loads(_at(root, entry["path"]).read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise FetchStopped(step, "model_inputs에 items가 없다")
    keys = sorted({k for it in items if isinstance(it, dict) for k in it})
    extra = [k for k in keys if k not in allowed]
    if extra:
        raise FetchStopped(step, f"model_inputs에 허용하지 않은 키: {', '.join(extra)}")
    return len(items)


def fetch_olist(root: Path, *, manifest: dict | None = None) -> list[str]:
    m = manifest or load_manifest()
    repo, commit = m["source"]["repository"], m["source"]["commit"]
    files = m["files"]
    for item in files:  # 받기 전에 경로를 모두 검사한다
        _at(root, item["path"])
    steps = [preflight(root, repo)]
    results = []
    for item in files:
        r = _download(root, repo, commit, item)
        name = Path(item["path"]).name
        results.append(f"{name} 건너뜀" if r == "건너뜀" else f"{name} 받음({megabytes(item['size'])} MB)")
    steps.append(f"받기(커밋 {commit[:8]}): {', '.join(results)}")
    inputs = next(it for it in files if it["role"] == "model_inputs")
    count = _check_model_inputs(root, inputs, m["model_inputs_allowed_item_keys"])
    steps.append(f"해시: {len(files)}개 일치, 모델 입력 {count}문항(정답 키 없음)")
    public_dir = str(Path(inputs["path"]).parent)
    gold_path = next(it for it in files if it["role"] == "gold")["path"]
    steps.append(f"공개 쪽: {public_dir}/ (DB·모델 입력·평가셋 manifest·스키마 스냅숏)")
    steps.append(f"정답 쪽: {gold_path} (채점 단계만 읽는다)")
    return format_summary("getolist 완료", steps, m["license_note"])
