"""No module in src/ or eval/ may import anything from tools/ (labeling aids and setup scripts)."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL_NAMES = sorted(p.stem for p in (REPO / "tools").glob("*.py"))


def _pattern(names):
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(
        rf"^\s*(?:import\s+(?:tools\.)?(?:{alt})\b|from\s+(?:tools\.)?(?:{alt})\s+import\b"
        rf"|from\s+tools\s+import\b|import\s+tools\b)", re.MULTILINE)


def test_pattern_catches_imports_and_ignores_mentions():
    pat = _pattern(["bench_dense"])
    assert pat.search("import bench_dense") and pat.search("from tools.bench_dense import main")
    assert pat.search("from tools import anything") and pat.search("    import tools")
    assert not pat.search("# see tools/bench_dense.py") and not pat.search("import tools_extra")


def test_src_and_eval_import_nothing_from_tools():
    assert TOOL_NAMES, "tools/ should contain scripts"
    pat = _pattern(TOOL_NAMES)
    offenders = [str(p.relative_to(REPO)) for d in ("src", "eval") for p in (REPO / d).rglob("*.py")
                 if pat.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
