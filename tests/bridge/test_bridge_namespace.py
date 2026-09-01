"""import 命名空间：**用户项目里的模块必须永远赢**（ADR 0020 §3）。

这是 native bridge 最容易安静出错的一处。safe worker 把 engine 目录永久插在
`sys.path[0]` 上并平铺 `import manifest / overrides / patchspec`——在它自己
的进程里没问题（cwd 是沙盒、脚本由我们挑的解释器跑）。native bridge 在
**用户的进程**里跑**用户的代码**，同一手就是数据损坏级的缺陷：

    用户项目/manifest.py   ← 他的"实验清单"
    用户项目/figure.py     →  import manifest

engine 目录留在 path 上的话，他拿到的是 Tavotto 的 manifest.py，而报出来的
AttributeError 指向完全错误的方向。

三条不变量，每条都有对应的变异反证（见文件末尾的注释）。
"""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest

from support.bridgekit import run_runner, write
from tavotto.engine import bridge

ENGINE_DIR = Path(bridge.RUNNER_PY).parent

#: 用户项目里**恰好与 Tavotto 引擎同名**的模块。前四个是 prompt 点名的，
#: 后几个是引擎里其余的平铺模块——名字越像，撞上的概率越高。
COLLIDING = (
    "manifest",
    "overrides",
    "config",
    "runtime",
    "figcapture",
    "patchspec",
    "pathgeom",
    "figsession",
    "wireproto",
    "worker",
    "bridge_runner",
    "bridgeboot",
)


def _user_project(tmp_path: Path) -> Path:
    proj = tmp_path / "paper"
    for name in COLLIDING:
        write(proj / f"{name}.py", f'WHOSE = "user"\nNAME = {name!r}\n')
    return proj


# ===========================================================================
# 进程内：装载器本身的不变量（第一阶段是纯标准库，不需要 matplotlib）
# ===========================================================================
def _boot():
    """按 bridge_runner 的做法加载 bridgeboot（不靠 sys.path）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "tavotto_bridge_boot_test", ENGINE_DIR / "bridgeboot.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_loading_restores_sys_path_exactly():
    """装载窗口开在 `sys.path` 上，**关的时候必须逐项还原**。

    留一条 engine 目录在那儿，用户之后每一次 import 都要先跟我们的模块比一遍。
    """
    boot = _boot()
    before = list(sys.path)
    boot.load_engine_modules(str(ENGINE_DIR), ("figcapture", "patchspec"))
    assert sys.path == before


def _load_phases() -> tuple[str, ...]:
    """`bridge_runner` 真正装进用户进程的那两批（`_PHASE1` + `_PHASE2`）。

    用 AST 读而不是 import：`bridge_runner` 一被 import 就会**当场跑装载**
    （模块级 `_PKG = bridgeboot.load_engine_modules(...)`），在测试进程里
    那会真的去动 `sys.modules`。
    """
    import ast

    tree = ast.parse((ENGINE_DIR / "bridge_runner.py").read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in ("_PHASE1", "_PHASE2") for t in node.targets
        ):
            out += [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
    assert out, "用例前提：bridge_runner 里确实有 _PHASE1/_PHASE2 两批装载清单"
    return tuple(out)


def test_load_and_restore_lists_cover_the_whole_flat_import_closure():
    """bridge 会平铺 import 到的**整条传递闭包**，两张表都要盖住。

    与 `test_runtime_build.py::test_spec_ships_every_module_the_worker_imports`
    同一个判据形状，同一个理由：**这两张表不能靠人记得改**。

    **两个维度，缺一不可**：

    | 表 | 它保证的事 | 漏了会怎样 |
    |---|---|---|
    | `_PHASE1` + `_PHASE2` | 该进私有包的都进了 | 那个模块以**真·顶层模块**的身份装在用户进程里：`__name__` 不带包前缀、`pkg.<name>` 取不到它、第二个消费者会再装一份（`load_engine_modules` 注释里那个「两份 figcapture」） |
    | `_TOPLEVEL_TO_RESTORE` | 顶层名字还得回去 | 用户项目里有同名模块时我们拿到**他那份**（一个指向完全错误方向的 AttributeError）；他没有时，Tavotto 那份留在他的顶层 `sys.modules` 里，他之后再也 import 不到自己那份 |

    2026-08-28 `figsession` 新增 `import previewbudget` 时**两张表都漏了**，
    而当时的门禁只查第二个维度里的一半（`ENGINE_SIBLINGS`），第一个维度它
    根本没问——抓住它的是人工评审。2026-08-29 补第一个维度时，原先那句
    「闭包比装载清单大」的前提当场变假：那句话本身就是**第一个维度被违反**
    的表现，它当初被写成了用例的前提（[[gate-pinned-on-one-side-only]]）。
    """
    import ast

    boot = _boot()

    def flat_imports(name: str) -> set[str]:
        tree = ast.parse((ENGINE_DIR / f"{name}.py").read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {
            n.module
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module
        }
        # 只要**同目录**的平铺模块：`import matplotlib` 这类第三方不在此列
        return {x for x in names if (ENGINE_DIR / f"{x}.py").is_file()}

    # **根取自真实的装载清单**，不是 `ENGINE_SIBLINGS`。
    #
    # 决定「装什么进用户进程」的是 `bridge_runner` 的 `_PHASE1` / `_PHASE2`
    # （硬编码两批，见那里的两阶段说明）；`ENGINE_SIBLINGS` 只被
    # `_TOPLEVEL_TO_RESTORE` 消费，两者**是两份清单**（siblings 里没有
    # figsession / wireproto）。拿 siblings 当根等于问错了问题——它少了两个
    # 真正会被装进去的模块，只是这条用例当初手工补了那两个名字才碰巧对。
    closure: set[str] = set()
    todo = [*_load_phases()]
    while todo:
        name = todo.pop()
        if name in closure:
            continue
        closure.add(name)
        todo += list(flat_imports(name))

    # 用例前提：AST 反推真的穿过了模块之间的边（否则下面两条在测一个恒真式：
    # 闭包 == 根集合，两条 issubset 自动成立）。两条边都取自实际发生过的漏项。
    assert "pathgeom" in flat_imports("manifest"), "用例前提：manifest 平铺 import pathgeom"
    assert "previewbudget" in flat_imports("figsession"), (
        "用例前提：figsession 平铺 import previewbudget（2026-08-28 漏的就是这条边）"
    )

    # 维度一：**该进私有包的都进了**
    unloaded = closure - set(_load_phases())
    assert not unloaded, (
        f"bridge_runner._PHASE1/_PHASE2 漏了引擎会平铺 import 的模块: "
        f"{sorted(unloaded)}——它们会以真·顶层模块的身份装在用户进程里，"
        f"私有包里取不到，第二个消费者还会再装一份"
    )

    # 维度二：**顶层名字还得回去**
    missing = closure - set(boot._TOPLEVEL_TO_RESTORE)
    assert not missing, (
        f"bridgeboot._TOPLEVEL_TO_RESTORE 漏了引擎会平铺 import 的模块: "
        f"{sorted(missing)}——漏掉的那个要么让我们拿到用户的同名模块，"
        f"要么把我们的留在他的顶层 sys.modules 里"
    )


def test_loading_leaves_no_toplevel_engine_names_behind():
    """装完之后 `sys.modules` 里不许多出任何顶层引擎名字。

    多出来一个，用户项目里的同名模块就永远 import 不到自己那份——
    `import manifest` 命中 `sys.modules["manifest"]` 之后连 finder 都不会走。
    """
    boot = _boot()
    before = {n for n in boot.ENGINE_SIBLINGS if n in sys.modules}
    boot.load_engine_modules(str(ENGINE_DIR), ("figcapture", "patchspec"))
    after = {n for n in boot.ENGINE_SIBLINGS if n in sys.modules}
    assert after == before
    pkg = sys.modules[boot.PRIVATE_PKG]
    assert pkg.figcapture.__name__ == f"{boot.PRIVATE_PKG}.figcapture"


def test_two_phase_load_never_duplicates_a_module():
    """两阶段装载**不许装出第二份**同名模块。

    第一阶段只装纯标准库那两个（不许碰 matplotlib，否则 backend 决策就被
    我们抢了）；第二阶段装 matplotlib 那批。第一阶段结束时顶层名字已经被
    收回，第二阶段里 `figsession` 那句 `import figcapture` 于是会**再装一份**
    ——两份 figcapture 不会当场报错（常量字符串相等），它会在别处以「捕获表
    对不上」的形状出现，而那时没人会想到模块身份。
    """
    boot = _boot()
    pkg = boot.load_engine_modules(str(ENGINE_DIR), ("figcapture",))
    first = pkg.figcapture
    # patchspec 是第二批：装它的时候 figcapture 的顶层名已经被收回了
    pkg2 = boot.load_engine_modules(str(ENGINE_DIR), ("figcapture", "patchspec"))
    assert pkg2 is pkg
    assert pkg2.figcapture is first, "第二阶段装出了第二份 figcapture"


def test_a_failed_load_still_restores_the_user_namespace():
    """装载**抛异常**时，`sys.path` 与顶层模块名也必须还原。

    装引擎是会失败的（缺 numpy、matplotlib 版本不兼容、磁盘错误……）。那时
    用户的 `sys.path` 与顶层模块名还被我们挪着——他之后的 `import manifest`
    拿不到自己那份，报出来的错与真实原因（"引擎没装起来"）毫无关系。

    反证：把 `load_engine_modules` 里的还原从 `finally` 挪回顺序执行，本条当场红。
    """
    boot = _boot()
    sentinel = types.ModuleType("manifest")
    sentinel.WHOSE = "user"
    sys.modules["manifest"] = sentinel
    before_path = list(sys.path)
    try:
        with pytest.raises(ModuleNotFoundError):
            boot.load_engine_modules(str(ENGINE_DIR), ("figcapture", "no_such_engine_module"))
        assert sys.path == before_path, "装载失败之后 sys.path 没还原"
        assert sys.modules.get("manifest") is sentinel, "装载失败之后用户的模块没还回去"
    finally:
        sys.modules.pop("manifest", None)


# ===========================================================================
# 真进程：用户项目里全是同名模块，图照样画得出来
# ===========================================================================
@pytest.mark.usefixtures("clean_env")
def test_user_modules_win_over_the_engine_siblings(user_python, tmp_path):
    """12 个同名模块全在用户项目里，用户 `import X` 拿到的必须全是自己那份。

    脚本同时画一张图并 `plt.show()`——所以判断发生在**引擎已经装载完毕**
    之后，而不是一个"还没开始工作"的空进程里。
    """
    proj = _user_project(tmp_path)
    report = tmp_path / "report.json"
    imports = "\n".join(f"import {n}" for n in COLLIDING)
    checks = "\n".join(
        f'assert {n}.WHOSE == "user", "{n} 被 Tavotto 顶掉了: " + getattr({n}, "__file__", "?")'
        for n in COLLIDING
    )
    write(
        proj / "fig.py",
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        f"{imports}\n{checks}\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([1, 2, 3], [1, 4, 9])\n"
        "plt.show()\n"
        f"{checks}\n"  # 屏障（= 引擎全量装载 + instrument + render）之后再验一次
        "print('USER_MODULES_OK')\n",
    )
    r = run_runner(
        user_python, bridge.RUNNER_PY, target=proj / "fig.py", cwd=str(proj), report=report
    )
    assert r.returncode == 0, r.stderr
    assert "USER_MODULES_OK" in r.stdout, r.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["engine_dir_was_on_sys_path"] is True, (
        "用例前提：CPython 确实把脚本目录（= engine 目录）放进了 sys.path[0]"
    )
    assert data["engine_dir_on_sys_path_now"] is False
    # 名字**在**是对的（用户 import 了自己那份），判据是那些名字下的文件
    # 一个都不许是 Tavotto 的。
    files = data["toplevel_engine_module_files"]
    assert set(files) >= {"manifest", "overrides", "figcapture"}, files
    for name, path in files.items():
        assert Path(path).parent != ENGINE_DIR, f"{name} 指向了 Tavotto 的 {path}"
        assert Path(path).parent == proj, f"{name} 指向了意料之外的 {path}"
    assert [f["stem"] for f in data["figures"]] == ["fig"]


@pytest.mark.usefixtures("clean_env")
def test_the_late_manifest_import_resolves_inside_the_private_package(
    user_python, tmp_path, bridge_session
):
    """`overrides` 的 late import 在**用户代码之后**才执行——裸名会命中用户的文件。

    走到它的路径是刻意挑的：先把刻度定位改成 fixed 并给一串新值，再在**同一次
    全量 apply 里**改第 13 条刻度的文字。那条 gid 还不在 index 里（前提断言
    按**性质**钉住：`axes_0.xticklabels_12` 不在 build 时 instrument 的集合里，
    不数总条数——manifest 只登记画着的刻度之后，总数随 locator/视区走，
    抄一个数只会静默过期），`FigState.resolve()` 于是现解，而现解要
    `manifest._ordered_axes`。用户项目里正好有一个 `manifest.py`——裸
    `import manifest` 命中的就是它，`_ordered_axes` 不存在，apply 当场抛。

    **为什么不拿色条方向那处做判据**：`_refresh_axes_follow` 外面包着
    `except Exception: pass`（"少一条联动不该拦渲染"），裸 import 在那里是
    **静默**失败的——第一版就是拿它当判据的，变异跑完照样全绿。两处现在共用
    同一个 `_sibling()` 访问器，所以这一条覆盖了机制；只改另一处的变异由下面
    那条结构性守卫兜住。

    反证：把 `_sibling("manifest")._ordered_axes` 改回
    `from manifest import _ordered_axes`，本条当场红。
    """
    proj = _user_project(tmp_path)
    write(
        proj / "fig.py",
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([1, 2, 3], [1, 4, 9])\n"
        "plt.show()\n",
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        build = sess.ensure_built()
        stem = next(iter(build["stems"]))
        man = json.loads((sess.out_dir / f"{stem}.json").read_text(encoding="utf-8"))
        ticks = next(e for e in man["elements"] if e["gid"] == "axes_0.xticks")
        instrumented = {
            e["gid"] for e in man["elements"] if e["gid"].startswith("axes_0.xticklabels_")
        }
        # 前提是性质不是总数：现解那条路要求「第 13 条」在 build 时**没被**
        # instrument（否则 override 直接走 index，根本不经过 late import），
        # 同时 instrument 真的发生过（空集合说明刻度登记整个坏了，那是另一个
        # 缺陷，不该被本用例的 warnings 断言含混地接住）。
        assert instrumented, "用例前提：build 时一条刻度文字都没 instrument——刻度登记坏了"
        assert "axes_0.xticklabels_12" not in instrumented, (
            "用例前提：第 13 条刻度文字不能在 build 时就被 instrument，"
            "否则 override 不会走 FigState.resolve 的现解路径"
        )
        lo, hi = 1.0, 3.0  # 都落在数据范围里——越界的刻度 matplotlib 根本不画
        values = [lo + (hi - lo) * i / 14.0 for i in range(15)]

        resp = sess.override(
            stem,
            [
                {"gid": ticks["gid"], "prop": "major_mode", "value": "fixed"},
                {"gid": ticks["gid"], "prop": "major_values", "value": values},
                {"gid": "axes_0.xticklabels_12", "prop": "text", "value": "LATE"},
            ],
        )
        assert resp["warnings"] == [], f"现解刻度文字失败（late import 打偏了）: {resp['warnings']}"
        assert "LATE" in (sess.out_dir / f"{stem}.svg").read_text(encoding="utf-8")
        sess.resume()


def test_no_bare_sibling_import_survives_in_overrides():
    """结构性守卫：`overrides.py` 里不许再出现裸的 `import manifest`。

    上面那条行为判据只覆盖 `FigState.resolve` 那一处（另一处被
    `except Exception: pass` 吞掉，测不到）。这条按源码判，两处都盖得住，
    将来新加的第三处也盖得住——判据是"机制"，不是"某一行"。
    """
    src = (ENGINE_DIR / "overrides.py").read_text(encoding="utf-8")
    bad = re.findall(r"^\s*(?:from\s+manifest\s+import|import\s+manifest)\b", src, re.M)
    assert not bad, (
        f"overrides.py 里有 {len(bad)} 处裸的兄弟模块 import——native bridge 里"
        f'它们会命中用户项目自己的 manifest.py。用 `_sibling("manifest")`。'
    )
