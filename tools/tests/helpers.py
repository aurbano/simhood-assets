from pathlib import Path

from assets_lib import checker


def run(repo, **kw) -> dict:
    """The strict report for a test repository, without the Khronos validator (unit tests)."""
    kw.setdefault("gltf_validator", False)
    return checker.validate(repo if isinstance(repo, Path) else repo.root, **kw)


def rules(report: dict, kind: str = "errors") -> list[str]:
    return [e["rule"] for e in report[kind]]


def where(report: dict, rule: str, kind: str = "errors") -> list[dict]:
    return [e for e in report[kind] if e["rule"] == rule]


def row(report: dict, mid: str) -> dict:
    return next(m for m in report["models"] if m["id"] == mid)
