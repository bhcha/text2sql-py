"""임베딩 모델 다섯 파일 받기."""

from __future__ import annotations

from pathlib import Path

from t2s.fetch.download import fetch_file

STEP = "6단계"


def fetch_model(root: Path, manifest: dict, *, step: str = STEP) -> str:
    m = manifest["model"]
    results = []
    for name, sha in m["sha256"].items():
        results.append(
            fetch_file(
                dest=Path(root) / m["path"] / name,
                url=m["url_base"] + name,
                sha256=sha,
                size=None,
                label=f"모델 {name}",
                step=step,
            )
        )
    n = len(results)
    done = [r for r in results if r != "건너뜀"]
    if not done:
        return f"모델: 건너뜀({n}파일 해시 일치)"
    return f"모델: {len(done)}파일 받음, sha256 {n}/{n} 일치"
