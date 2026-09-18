"""指导文档的结构门禁：速查表 ↔ 细则一一对应，引用的路径 / 用例 / ADR 都真的在。

2026-09-17 的可维护性审计量到三份 `AGENTS.md` 原始字节 14.8 / 143.9 / 147.8 KB，而
Codex 沿「仓库根 → cwd」拼接项目指令、默认 `project_doc_max_bytes` 是 32 KiB——后两份
在自动加载的指令里从来没被完整读到过。治理的做法是**分层 + 索引 + 按需读取**：子级
`AGENTS.md` 改成速查表，每一行按改动路径指向 `docs/rules/<层>/<主题>.md` 里的全文。

这种结构靠两条对应关系活着，而两条都会在没人注意的时候断掉：

1. **细则文件没人指向 = 那批规则从此没人读到。** 新增一份细则却忘了在速查表里加行，
   或者速查表改了文件名而细则没跟着改名——表现都是「规则还在仓库里，但加载路径上
   已经没有它」。
2. **速查表里引用的用例 / 路径 / ADR 失效 = 看护列在撒谎。** 速查表是每次会话都读的
   那一层，它说「看护：`tests/test_x.py`」而那个文件已经改名，读的人会以为有门禁。

两条都是纯字符串对拍，不 import 产品代码，任何环境都跑得起来。判据刻意只认**能无歧义
解析的引用**（仓库根相对路径、`tests/` 下的用例、按文件名唯一的前端用例、ADR 号），
解析不了的（`<占位>`、通配、数据目录里的路径）不判——判不出就别判，别让一条门禁为了
覆盖率去猜。

体积不在这里判：预算是试行的、按实际加载路径量的，看 `scripts/dev/agents_budget.py`。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "docs" / "rules"

#: 速查表 → 它负责路由的细则目录。
SHEETS: dict[str, Path] = {
    "AGENTS.md": RULES / "repo",
    "src/tavotto/AGENTS.md": RULES / "backend",
    "web/AGENTS.md": RULES / "frontend",
    ".github/AGENTS.md": RULES / "ci",
}
#: 细则目录 = 速查表点名的那些（加一层只改上表）。
LAYERS = tuple(dict.fromkeys(v.name for v in SHEETS.values()))

BACKTICK = re.compile(r"`([^`\n]+)`")
ADR_REF = re.compile(r"ADR\s*0?(\d{3,4})")
#: 被判的路径形状：含 `/`、以已知扩展名结尾、没有占位 / 通配 / 空白。
PATH_EXT = (".py", ".ts", ".tsx", ".mjs", ".md", ".json", ".rs", ".css", ".yml", ".toml", ".sh")
SKIP_CHARS = set("<>*{}… ")
#: 首段属于这些的 token 才当仓库路径判（数据目录里的 `layouts/…`、`cache/…` 不在此列）。
REPO_FIRST_SEGMENTS = {
    "docs",
    "tests",
    "scripts",
    "src",
    "web",
    "codex-plugin",
    "packaging",
    "src-tauri",
    "workerd",
    ".github",
    "services",
    # 后端文本里的相对写法（相对 src/tavotto/）
    "engine",
    "pdfbackend",
    "profiles",
    "tavotto",
    # 前端文本里的相对写法（相对 web/src/ 或 web/）
    "store",
    "lib",
    "hooks",
    "canvas",
    "components",
    "diagnostics",
    "playground",
    "embedded",
    "i18n",
    "types",
    "e2e",
    "mcp",
    "test",
}
#: 相对写法可能落在的根，按顺序试。
RELATIVE_ROOTS = (ROOT, ROOT / "web", ROOT / "web" / "src", ROOT / "src", ROOT / "src" / "tavotto")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _rules_files(layer_dir: Path) -> list[Path]:
    return sorted(p for p in layer_dir.glob("*.md") if p.name != "README.md")


def _tokens(text: str) -> list[str]:
    out = []
    for tok in BACKTICK.findall(text):
        tok = tok.split("::")[0].strip()
        if tok:
            out.append(tok)
    return out


def _looks_like_repo_path(tok: str) -> bool:
    if "/" not in tok or any(c in SKIP_CHARS for c in tok):
        return False
    if not tok.endswith(PATH_EXT):
        return False
    return tok.split("/")[0] in REPO_FIRST_SEGMENTS


def _resolves(tok: str) -> bool:
    return any((root / tok).exists() for root in RELATIVE_ROOTS)


def _is_test_token(tok: str) -> bool:
    base = tok.rsplit("/", 1)[-1]
    return base.endswith((".test.ts", ".test.tsx", ".spec.ts")) or (
        base.startswith("test_") and base.endswith(".py")
    )


def _web_test_index() -> dict[str, list[Path]]:
    idx: dict[str, list[Path]] = {}
    for p in list((ROOT / "web" / "src").rglob("*.test.ts*")) + list(
        (ROOT / "web" / "e2e").glob("*.spec.ts")
    ):
        idx.setdefault(p.name, []).append(p)
    return idx


def _py_test_index() -> dict[str, list[Path]]:
    idx: dict[str, list[Path]] = {}
    for p in (ROOT / "tests").rglob("test_*.py"):
        idx.setdefault(p.name, []).append(p)
    return idx


# ---------------------------------------------------------------- 前提


def test_the_rules_corpus_is_actually_there():
    """判据的前提：真的扫到了一批细则。空集合上的「全部对应」恒真。"""
    backend = _rules_files(RULES / "backend")
    frontend = _rules_files(RULES / "frontend")
    repo = _rules_files(RULES / "repo")
    ci = _rules_files(RULES / "ci")
    assert len(backend) >= 25, f"backend 细则只有 {len(backend)} 份"
    assert len(frontend) >= 25, f"frontend 细则只有 {len(frontend)} 份"
    assert len(repo) >= 2, f"repo 细则只有 {len(repo)} 份"
    assert len(ci) >= 10, f"ci 细则只有 {len(ci)} 份"
    for sheet in SHEETS:
        assert (ROOT / sheet).is_file(), sheet


# ---------------------------------------------------------------- 一、一一对应


@pytest.mark.parametrize("sheet", sorted(SHEETS))
def test_every_rules_file_is_routed_from_its_sheet(sheet: str):
    """`docs/rules/<层>/` 里每一份细则，都得在那一层的速查表里被点名。

    没被点名的细则等于不存在：加载路径是「速查表 → 细则」，速查表不指过去，
    没有任何会话会读到它。
    """
    text = _read(sheet)
    layer_dir = SHEETS[sheet]
    orphans = []
    for path in _rules_files(layer_dir):
        rel = path.relative_to(ROOT).as_posix()
        if rel not in text and path.name not in text:
            orphans.append(rel)
    assert not orphans, f"{sheet} 没有指向这些细则（规则在仓库里、不在加载路径上）: {orphans}"


@pytest.mark.parametrize("sheet", sorted(SHEETS))
def test_every_rules_reference_in_a_sheet_resolves(sheet: str):
    """速查表里点名的细则文件必须真的在（改名漏改的另一半）。"""
    text = _read(sheet)
    layer_dir = SHEETS[sheet]
    broken = []
    for tok in _tokens(text):
        if tok.startswith("docs/rules/") and tok.endswith(".md"):
            if not (ROOT / tok).is_file():
                broken.append(tok)
        elif (
            "/" not in tok
            and tok.endswith(".md")
            and tok not in {"README.md", "AGENTS.md", "CLAUDE.md"}
        ):
            # 表里的短写：`brand-and-naming.md` 相对本层细则目录
            if not (layer_dir / tok).is_file():
                broken.append(f"{layer_dir.relative_to(ROOT).as_posix()}/{tok}")
    assert not broken, f"{sheet} 指向不存在的细则: {broken}"


def test_rules_files_declare_their_origin():
    """每份细则头部要说自己从哪份速查表迁出——读的人得知道该回哪里改那一行。"""
    missing = []
    for layer in LAYERS:
        for path in _rules_files(RULES / layer):
            head = path.read_text(encoding="utf-8").split("\n", 6)
            if not any("AGENTS.md" in ln for ln in head[:6]):
                missing.append(path.relative_to(ROOT).as_posix())
    assert not missing, f"这些细则头部没有交代出处（哪份 AGENTS.md 的哪一节）: {missing}"


# ---------------------------------------------------------------- 二、引用都在


def _all_guidance_files() -> list[Path]:
    files = [ROOT / s for s in SHEETS]
    for layer in LAYERS:
        files.extend(_rules_files(RULES / layer))
    return files


def test_repo_paths_cited_in_guidance_exist():
    """速查表与细则里写成路径形状的引用，目标文件必须在。

    只判能无歧义解析的形状（见模块 docstring）；相对写法按 `web/src` / `src/tavotto`
    等几个根依次试。
    """
    broken = []
    for path in _all_guidance_files():
        for tok in _tokens(path.read_text(encoding="utf-8")):
            if _is_test_token(tok) or not _looks_like_repo_path(tok):
                continue
            if not _resolves(tok):
                broken.append(f"{path.relative_to(ROOT).as_posix()} → {tok}")
    assert not broken, "这些路径引用指向不存在的文件:\n  " + "\n  ".join(broken)


def test_tests_cited_in_guidance_exist():
    """「看护：xxx」点名的用例文件必须真的在——看护列说谎比没有看护列更坏。

    前端用例按文件名在 `web/src/**` 与 `web/e2e/` 里找（文本里常用相对写法）；
    Python 用例按文件名在 `tests/**` 里找。
    """
    web_idx = _web_test_index()
    py_idx = _py_test_index()
    assert len(web_idx) > 100 and len(py_idx) > 100, "用例索引多半量在空集合上"
    broken = []
    for path in _all_guidance_files():
        for tok in _tokens(path.read_text(encoding="utf-8")):
            if not _is_test_token(tok) or any(c in SKIP_CHARS for c in tok):
                continue
            base = tok.rsplit("/", 1)[-1]
            idx = py_idx if base.endswith(".py") else web_idx
            if base not in idx and not _resolves(tok):
                broken.append(f"{path.relative_to(ROOT).as_posix()} → {tok}")
    assert not broken, "这些用例引用指向不存在的文件:\n  " + "\n  ".join(broken)


def test_adrs_cited_in_guidance_exist():
    """「ADR 00NN」必须对应 `docs/adr/00NN-*.md`。"""
    existing = {p.name[:4] for p in (ROOT / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")}
    assert len(existing) >= 40
    broken = []
    for path in _all_guidance_files():
        for num in ADR_REF.findall(path.read_text(encoding="utf-8")):
            if num.zfill(4) not in existing:
                broken.append(f"{path.relative_to(ROOT).as_posix()} → ADR {num}")
    assert not broken, f"这些 ADR 引用没有对应文件: {sorted(set(broken))}"
