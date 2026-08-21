"""浏览器 playground 产物的**构建侧**看护：engine.zip 装了什么。

与 `test_runtime_build.py` 在桌面那条链路上的地位相同——盯的是「不联网也能
出错、而且只有到了用户手里才暴露」的那一类。纯标准库、无 skip：这条门禁
在只装了 flask+pymupdf 的 `.venv` 上也必须跑得动。放进
`test_browser_session.py` 就不行，那个文件模块级 skipif 没有 matplotlib 的
解释器——一条会静默跳过的门禁比没有门禁更坏，它在报平安。
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "src" / "tavotto" / "engine"
sys.path.insert(0, str(REPO / "scripts"))

import build_browser_playground as bbp  # noqa: E402


def _flat_imports(path: Path) -> set[str]:
    """一个模块里**平铺 import** 的同目录兄弟（`import manifest` 这种）。

    判据与 worker 那条同源：名字对得上 `engine/<name>.py` 才算，
    `matplotlib` 这类第三方不在此列。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {a.name for node in ast.walk(tree) if isinstance(node, ast.Import)
             for a in node.names}
    names |= {node.module for node in ast.walk(tree)
              if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module}
    return {n for n in names if (ENGINE / f"{n}.py").is_file()}


def _closure(*roots: str) -> set[str]:
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += list(_flat_imports(ENGINE / f"{name}.py"))
    return seen


def test_engine_zip_ships_every_module_the_browser_imports():
    """`ENGINE_FILES` 必须盖住 `browser.py` 平铺 import 的**整条传递闭包**。

    engine.zip 是白名单，不是「把 engine/ 打包」：Pyodide 里 `sys.path` 只有
    解开的 `/engine`，漏一个模块的表现是
    **下载完十几 MB 科学栈之后**才在 `pyimport('browser')` 上
    ModuleNotFoundError——而 pytest / vitest / tsc 全绿，因为
    `test_browser_session.py` 的驱动把真实的 `src/tavotto/engine` 目录塞进了
    sys.path，`import <兄弟>` 当然找得到。唯一拦得住的是真 Pyodide 的 e2e，
    而它要先重建 dist-playground 才跑得到（2026-08-21 `figcapture.py` 就是
    这么差点漏出去的）。

    **必须是传递闭包，不能只看 browser.py 一层**——桌面那条
    （`test_spec_ships_every_module_the_worker_imports`）已经用
    `manifest → pathgeom` 交过一次学费：只查半条链的门禁在报平安。
    """
    need = {f"{n}.py" for n in _closure("browser", "browser_imports")}
    # 用例前提：闭包确实穿透了不止一层（pathgeom 是 manifest 引进来的，
    # browser.py 自己并不直接 import 它）
    assert "pathgeom.py" in need, "用例前提失效：manifest 不再平铺 import pathgeom？"
    assert "browser.py" not in _flat_imports(ENGINE / "manifest.py"), \
        "引擎模块反过来 import browser 会让这条闭包失去意义"

    missing = need - set(bbp.ENGINE_FILES)
    assert not missing, (
        f"scripts/build_browser_playground.py 的 ENGINE_FILES 漏了 browser.py "
        f"要用的模块: {sorted(missing)}。加平铺 import 就要同步加白名单。")


def test_engine_files_all_exist_and_are_sorted():
    """白名单里的每一项都得是真文件；顺序固定，zip 才是确定性产物。"""
    for name in bbp.ENGINE_FILES:
        assert (ENGINE / name).is_file(), f"ENGINE_FILES 里的 {name} 不存在"
    assert bbp.ENGINE_FILES == sorted(bbp.ENGINE_FILES), \
        "ENGINE_FILES 请保持排序——构建脚本按名排序写 zip，清单同序才好读 diff"


def test_fingerprint_covers_every_shipped_engine_module():
    """指纹的输入集必须包含 engine.zip 里的每一个模块。

    否则会出现最难查的一种过期：模块改了、装进 zip 的是新的、指纹却没动，
    于是 `--check` 与网站的 `check-playground` 双双报「与源码一致」，
    而用户装到的画布行为已经变了。
    """
    src = (REPO / "scripts" / "build_browser_playground.py").read_text(encoding="utf-8")
    assert "ENGINE / name for name in ENGINE_FILES" in src, (
        "source_fingerprint() 不再按 ENGINE_FILES 收引擎模块了——"
        "白名单与指纹输入必须是同一份清单，分开写迟早漏。")
